import json
import sqlite3
import time
from pathlib import Path

import httpx
import pytest
from PIL import Image, ImageChops, ImageOps
from pydantic import ValidationError
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from app.models import Layout, Provider, WorkRequest
from app.vision import read_image, crop_layout, check_layout, import_legacy
from app.gateway import Gateway, BudgetExceeded, function_schema, user_images
from app.workflow import FigureWorkflow


def layout():
    return Layout(image_size=[100,80],panels=[{"id":"p_A","label":"A","rect":[0,0,45,80]}, {"id":"p_B","label":"B","rect":[55,0,100,80]}])


@pytest.mark.parametrize("rect", [[-1,0,20,20],[0,0,0,10],[0,0,101,20],[20,10,10,20]])
def test_rect_validation(rect):
    with pytest.raises(ValidationError):
        Layout(image_size=[100,80],panels=[{"id":"p_A","label":"A","rect":rect}])


def test_shared_reference_and_label_validation():
    data=layout().model_dump()
    data["shared_regions"]=[{"id":"s","label":"title","rect":[0,0,10,10],"panel_ids":["missing"]}]
    with pytest.raises(ValidationError): Layout.model_validate(data)
    data["shared_regions"]=[];data["panels"][1]["label"]="A"
    with pytest.raises(ValidationError): Layout.model_validate(data)


def test_crop_matches_decoded_source(tmp_path):
    source=tmp_path/"source.png"
    image=Image.effect_noise((100,80),90).convert("RGB");image.save(source)
    result=crop_layout(str(source),layout(),tmp_path/"version",{"available":True,"boxes":[]})
    for p in layout().panels:
        actual=Image.open(Path(result["directory"])/f"panel_{p.id}.png").convert("RGB")
        assert ImageChops.difference(actual,image.crop(p.rect)).getbbox() is None
    with pytest.raises(FileExistsError): crop_layout(str(source),layout(),tmp_path/"version")


def test_text_crossing_detected_and_unavailable_not_success():
    issues=check_layout(layout(),{"available":True,"boxes":[{"text":"axis","rect":[40,5,60,15]}]})
    assert any(i["code"]=="TEXT_CHECK_UNAVAILABLE" for i in issues)
    assert not any(i["code"]=="TEXT_INTERSECTION" for i in issues)
    assert any(i["code"]=="OCR_UNAVAILABLE" for i in check_layout(layout()))


@pytest.mark.parametrize("protocol",["chat","responses"])
def test_wire_protocol_tool_roundtrip(monkeypatch,tmp_path,protocol):
    source=tmp_path/"source.png";Image.new("RGB",(100,80),"white").save(source)
    requests=[]
    def post(url,**kwargs):
        requests.append(kwargs["json"])
        if protocol=="chat":
            value={"choices":[{"message":{"role":"assistant","tool_calls":[{"id":"c1","type":"function","function":{"name":"inspect","arguments":"{}"}}]}}]}
        else:
            value={"output":[{"type":"function_call","id":"fc1","call_id":"c1","name":"inspect","arguments":"{}"}]}
        return httpx.Response(200,json=value,request=httpx.Request("POST",url))
    monkeypatch.setattr(httpx,"post",post)
    g=Gateway(Provider(base_url="http://fixture.invalid/v1",protocol=protocol,model="synthetic",api_key="canary"),{})
    tools=[function_schema("inspect","test",{"type":"object","properties":{}})]
    messages=[user_images("image",[str(source)])]
    first=g.invoke(messages,tools);assert first["calls"][0]["name"]=="inspect"
    messages += [first["native"],{"role":"tool","tool_call_id":"c1","content":"observed"}]
    g.invoke(messages,tools)
    assert "image_ref" not in json.dumps(requests)
    if protocol=="chat":
        assert requests[1]["messages"][-1]["tool_call_id"]=="c1"
        assert requests[0]["messages"][0]["content"][1]["type"]=="image_url"
    else:
        assert requests[1]["input"][-1]["type"]=="function_call_output"
        assert requests[0]["input"][0]["content"][1]["type"]=="input_image"
    assert "image_ref" in json.dumps(messages)


def test_budget_blocks_network():
    g=Gateway(Provider(base_url="http://fixture.invalid",model="fixture"),{"calls":24,"started":time.time()})
    with pytest.raises(BudgetExceeded):g.post({})


def request(tmp_path):
    source=tmp_path/"source.png";Image.new("RGB",(100,80),"white").save(source)
    return WorkRequest(image_id="test_1",source=str(source),output_dir=str(tmp_path/"output"),provider=Provider(base_url="http://fixture.invalid",model="fixture",api_key="CHECKPOINT_SECRET_CANARY",verified=True))


def test_real_graph_human_interrupt_survives_reopen(monkeypatch,tmp_path):
    req=request(tmp_path);db=tmp_path/"checkpoint.sqlite"
    def unavailable(*args,**kwargs):raise RuntimeError("synthetic unavailable model")
    monkeypatch.setattr(Gateway,"invoke",unavailable)
    with sqlite3.connect(db,check_same_thread=False) as connection:
        flow=FigureWorkflow(req,SqliteSaver(connection));flow.graph.invoke({},flow.config())
        assert "human" in flow.graph.get_state(flow.config()).next
    with sqlite3.connect(db,check_same_thread=False) as connection:
        flow=FigureWorkflow(req,SqliteSaver(connection))
        result=flow.graph.invoke(Command(resume={"action":"confirm","layout":layout().model_dump()}),flow.config())
        assert result["status"]=="CONFIRMED"
        assert Path(result["candidate_dir"],"panel_p_A.png").exists()
    assert b"CHECKPOINT_SECRET_CANARY" not in db.read_bytes()


def test_react_tools_actual_crop_then_review(monkeypatch,tmp_path):
    req=request(tmp_path)
    monkeypatch.setattr("app.workflow.detect_text",lambda p:{"available":True,"boxes":[]})
    actions=[("analyze_structure",{}),("propose_layout",{"layout":layout().model_dump()}),("inspect_crops",{"panel_ids":["p_A","p_B"]}),None]
    def fixture_invoke(self,messages,tools,force=None):
        self.state["calls"]=self.state.get("calls",0)+1
        action=("submit_review",{"accepted":True,"issues":[]}) if force else actions.pop(0)
        calls=[] if action is None else [{"id":"c"+str(self.state["calls"]),"name":action[0],"arguments":json.dumps(action[1])}]
        native={"role":"assistant","content":"candidate ready"} if not calls else {"role":"assistant","tool_calls":[{"type":"function","id":calls[0]["id"],"function":{"name":calls[0]["name"],"arguments":calls[0]["arguments"]}}]}
        return {"calls":calls,"native":native,"text":""}
    monkeypatch.setattr(Gateway,"invoke",fixture_invoke)
    with sqlite3.connect(":memory:",check_same_thread=False) as connection:
        flow=FigureWorkflow(req,SqliteSaver(connection));result=flow.graph.invoke({},flow.config())
        assert result["status"]=="AUTO_ACCEPTED"
        assert result["proposals"]==1 and result["calls"]==5
        assert {e["action"] for e in result["events"]}>={"analyze_structure","propose_layout","inspect_crops","review","finish"}


def test_legacy_size_and_source_checked(tmp_path):
    source=tmp_path/"15.png";Image.new("RGB",(100,80)).save(source)
    legacy=tmp_path/"layout.json";legacy.write_text(json.dumps({"source":r"D:\old\15.png","image_size":[100,80],"panels":{"A":{"rect":[0,0,45,80]}}}))
    assert import_legacy(str(source),legacy).panels[0].label=="A"
    bad=json.loads(legacy.read_text());bad["source"]=r"D:\old\14.png";legacy.write_text(json.dumps(bad))
    with pytest.raises(ValueError):import_legacy(str(source),legacy)
