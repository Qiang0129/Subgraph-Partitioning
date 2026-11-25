"""Geometric/label regression only; never presents IoU as semantic completeness accuracy."""
import argparse
import json
import zipfile


def iou(a,b):
    inter=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
    union=(a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-inter
    return inter/union if union else 0


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gold',required=True);parser.add_argument('--pred',required=True);parser.add_argument('--output')
    args=parser.parse_args()
    gold={i['sha256']:i for i in json.load(open(args.gold,encoding='utf-8'))['images']}
    report={'note':'Label and geometry checks only. Content integrity, false auto-acceptance and shared semantics require human audit.','images':[]}
    with zipfile.ZipFile(args.pred) as archive:
        manifest=json.loads(archive.read('manifest.json'))
        for item in manifest['images']:
            expected=gold.get(item.get('sha256'))
            if not expected:continue
            result={'name':item['name'],'status':item['status']}
            if not item.get('directory'):
                result['missing_output']=True;report['images'].append(result);continue
            predicted=json.loads(archive.read(item['directory']+'layout.json'))
            truth={p['label']:p for p in expected['layout']['panels']};actual={p['label']:p for p in predicted['panels']}
            common=truth.keys()&actual.keys()
            result.update(label_precision=len(common)/max(1,len(actual)),label_recall=len(common)/max(1,len(truth)),
                          iou_by_label={label:iou(truth[label]['rect'],actual[label]['rect']) for label in common})
            report['images'].append(result)
    text=json.dumps(report,ensure_ascii=False,indent=2)
    if args.output:open(args.output,'w',encoding='utf-8').write(text)
    print(text)


if __name__=='__main__':main()
