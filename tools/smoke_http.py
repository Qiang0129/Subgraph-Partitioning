"""Isolated Java + Python HTTP integration, no real model calls or credentials."""
import argparse
import io
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import socket
import subprocess
import sys
import time
import zipfile

import httpx
from PIL import Image, ImageChops, ImageDraw

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'runtime'/'integration'/str(int(time.time()))
BASE='http://127.0.0.1:8775/api'


def main(real_ocr=False):
    for port in (8775,8776):
        with socket.socket() as probe:
            if os.name=='nt':probe.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
            probe.bind(('127.0.0.1',port))
    DATA.mkdir(parents=True)
    env={**os.environ,'SFA_DATA':str(DATA),'SFA_INTERNAL_TOKEN':secrets.token_hex(32),'SFA_PORT':'8775',
         'SFA_AGENT_URL':'http://127.0.0.1:8776','SFA_LEGACY':str(ROOT.parent/'子图划分结果'/'result12'),
         'LANGCHAIN_TRACING_V2':'false','LANGSMITH_TRACING':'false'}
    children=[];logs=[];checks=[]
    def check(condition,name):
        assert condition,name
        checks.append(name)
    def request(method,path,expected=200,**kwargs):
        kwargs.setdefault('timeout',40)
        response=httpx.request(method,BASE+path,**kwargs)
        assert response.status_code==expected,(path,response.status_code,response.text[:500])
        return response
    try:
        for name,args in [('agent',[sys.executable,'-m','uvicorn','app.main:app','--app-dir',str(ROOT/'agent'),'--host','127.0.0.1','--port','8776']),
                          ('java',[shutil.which('java'),'-jar',str(ROOT/'backend'/'target'/'subfigure-backend-0.1.0.jar')])]:
            log=open(DATA/f'{name}.log','wb');logs.append(log)
            children.append(subprocess.Popen(args,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)))
        for _ in range(100):
            try:
                if httpx.get(BASE+'/health',timeout=2).json().get('agent'):break
            except Exception:pass
            time.sleep(.7)
        else:raise AssertionError('QA services failed to start: '+str(DATA))
        check(True,'isolated Java and Python readiness')
        batch=request('POST','/batches',json={'name':'isolated synthetic integration'}).json()['id']
        picture=Image.new('RGB',(200,100),'white');draw=ImageDraw.Draw(picture)
        draw.rectangle([0,20,89,99],fill='green');draw.rectangle([110,20,199,99],fill='red');draw.text((60,0),'Shared title',fill='black')
        buffer=io.BytesIO();picture.save(buffer,format='PNG')
        item=request('POST',f'/batches/{batch}/images',files={'files':('synthetic.png',buffer.getvalue(),'image/png')}).json()[0]
        image_id=item['id'];check(item['image_size']==[200,100],'upload normalizes original size')
        layout={'image_size':[200,100],'panels':[{'id':'p_A','label':'A','rect':[0,20,90,100]},{'id':'p_B','label':'B','rect':[110,20,200,100]}],
                'shared_regions':[{'id':'s_1','label':'title','rect':[50,0,150,18],'panel_ids':['p_A','p_B']}]}
        invalid=json.loads(json.dumps(layout));invalid['panels'][0]['rect'][2]=201
        request('POST',f'/images/{image_id}/layout',expected=400,json={'revision':0,'layout':invalid})
        check(True,'out-of-bounds layout rejected')
        draft=request('POST',f'/images/{image_id}/layout',json={'revision':0,'layout':layout,'confirm':False}).json()
        check(draft['revision']==1 and draft['status']=='DRAFT','draft version saved')
        request('POST',f'/images/{image_id}/layout',expected=409,json={'revision':0,'layout':layout})
        check(True,'stale manual edits rejected')
        archive=zipfile.ZipFile(io.BytesIO(request('GET',f'/batches/{batch}/export').content))
        check(archive.namelist()==['manifest.json'],'unconfirmed draft excluded from final ZIP')
        draft_zip=zipfile.ZipFile(io.BytesIO(request('GET',f'/batches/{batch}/export?drafts=true').content))
        check(any(n.startswith('draft/') for n in draft_zip.namelist()),'draft download separately marked')
        confirmation=request('POST',f'/images/{image_id}/layout',json={'revision':1,'layout':layout,'confirm':True,'acknowledge_warnings':True,'ocr':False}).json()
        check(confirmation['status']=='CONFIRMED' and confirmation['revision']==2,'explicit manual confirmation saved')
        final=zipfile.ZipFile(io.BytesIO(request('GET',f'/batches/{batch}/export').content))
        manifest=json.loads(final.read('manifest.json'));directory=manifest['images'][0]['directory']
        actual=Image.open(io.BytesIO(final.read(directory+'panel_p_A.png'))).convert('RGB')
        check(ImageChops.difference(actual,picture.crop((0,20,90,100))).getbbox() is None,'exported crop is pixel-exact')
        exported=json.loads(final.read(directory+'layout.json'))
        check(exported['source']['name']=='synthetic.png' and exported['source']['sha256']==item['sha256'],'standalone layout preserves original provenance')
        check(exported['shared_regions'][0]['panel_ids']==['p_A','p_B'],'shared title associations exported')
        request('POST',f'/images/{image_id}/gold',json={'notes':'synthetic QA only'})
        check(len(request('GET','/gold').json()['images'])==1,'gold export requires manual confirmation')
        provider=request('POST','/providers',json={'name':'synthetic unverified fixture','base_url':'http://127.0.0.1:1/v1','model':'fixture','protocol':'chat','api_key':'SYNTHETIC_NOT_A_REAL_KEY'}).json()
        check('api_key' not in provider and 'key_cipher' not in provider and provider['has_key'],'public provider projection hides key material')
        with sqlite3.connect(DATA/'app.sqlite') as connection:
            # Query the live database so uncheckpointed WAL pages are included.
            payload='\n'.join(connection.iterdump())
        check('SYNTHETIC_NOT_A_REAL_KEY' not in payload,'live database including WAL does not store plaintext provider key')
        request('POST',f'/batches/{batch}/start',expected=400,json={'provider_id':provider['id']})
        check(True,'unverified provider cannot start model work')
        with httpx.stream('GET',BASE+'/events',timeout=8) as stream:
            check(any(line.startswith('event:tick') or line.startswith('event: tick') for line in stream.iter_lines() if line),'SSE progress channel responds')
        original=ROOT.parent/'图片'/'15.png'
        if original.exists():
            raw=original.read_bytes()
            real=request('POST',f'/batches/{batch}/images',files={'files':('15.png',raw,'image/png')}).json()[0]
            imported=request('POST',f'/images/{real["id"]}/legacy',json={'revision':0}).json()
            check(len(imported['layout']['panels'])==10 and imported['status']=='DRAFT','existing real layout imported only as a draft')
            if real_ocr:
                checked=request('POST',f'/images/{real["id"]}/layout',timeout=180,json={
                    'revision':imported['revision'],'layout':imported['layout'],'confirm':False,'ocr':True}).json()
                check(not any(i['code']=='OCR_UNAVAILABLE' for i in checked['issues']),'real PaddleOCR works through Java and Python HTTP services')
                check(any(i['code']=='TEXT_INTERSECTION' and 'MC-3T3-E1' in i['message'] for i in checked['issues']),
                      'real OCR flags the legacy shared-title cut on image 15')
            check(original.read_bytes()==raw,'user original remains unchanged')
        elif real_ocr:raise AssertionError('--ocr requires the local image 15 fixture')
        result={'passed':len(checks),'checks':checks,'data_directory':str(DATA),'real_ocr_tested':real_ocr,'model_quality_tested':False}
        (DATA/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(result,ensure_ascii=False,indent=2))
    finally:
        for child in children:
            if child.poll() is None:
                if os.name=='nt':
                    subprocess.run(['taskkill','/PID',str(child.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=False)
                else:child.terminate()
        for child in children:
            try:child.wait(timeout=15)
            except subprocess.TimeoutExpired:child.kill()
        for log in logs:log.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ocr',action='store_true',help='Also run real PaddleOCR on local image 15 (may download weights)')
    main(parser.parse_args().ocr)
