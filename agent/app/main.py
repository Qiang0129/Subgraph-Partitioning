import hashlib
import json
import os
import secrets
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from PIL import Image, ImageDraw, ImageFont

from .gateway import Gateway, function_schema, user_images, list_models
from .models import Layout, Provider, ProviderConnection, WorkRequest
from .vision import atomic_json, crop_layout, detect_text, import_legacy, read_image
from .workflow import FigureWorkflow

ROOT = Path(os.environ.get("SFA_DATA", "runtime")).resolve()
TOKEN = os.environ.get("SFA_INTERNAL_TOKEN", "")
POOL = ThreadPoolExecutor(max_workers=2)
FUTURES, CANCELS = {}, {}
LOCK = threading.Lock()
SAVER = None


def inside(path: str) -> Path:
    resolved = Path(path).resolve()
    if not resolved.is_relative_to(ROOT):
        raise HTTPException(400, "Path is outside application data")
    return resolved


def identifier(value: str):
    if not value or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in value):
        raise HTTPException(400, "Invalid identifier")
    return value


def auth(x_internal_token: str = Header(default="")):
    if not TOKEN or not secrets.compare_digest(x_internal_token, TOKEN):
        raise HTTPException(401, "Internal service authentication required")


@asynccontextmanager
async def lifespan(app):
    global SAVER
    if len(TOKEN) < 24:
        raise RuntimeError("SFA_INTERNAL_TOKEN must contain at least 24 characters; use start.ps1")
    ROOT.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(ROOT / "agent-checkpoints.sqlite", check_same_thread=False)
    connection.execute("PRAGMA journal_mode=WAL")
    SAVER = SqliteSaver(connection)
    yield
    POOL.shutdown(wait=True, cancel_futures=True)
    connection.close()


app = FastAPI(title="Subgraph Partitioning Agent", lifespan=lifespan, dependencies=[Depends(auth)])


@app.get("/health")
def health():
    return {"status": "ok", "protocols": ["chat", "responses"], "runtime": "LangGraph ReAct"}


@app.post("/normalize")
def normalize(payload: dict):
    source, target = inside(payload["source"]), inside(payload["target"])
    with Image.open(source) as original:
        original_size = list(original.size)
        orientation = int(original.getexif().get(274, 1))
    image = read_image(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target)
    return {"image_size": list(image.size), "original_size": original_size, "exif_orientation": orientation,
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}


@app.post("/probe")
def probe(provider: Provider):
    folder = ROOT / "probes"
    folder.mkdir(exist_ok=True)
    path = folder / f"{uuid.uuid4().hex}.png"
    code = str(secrets.randbelow(9000)+1000)
    image = Image.new("RGB", (180, 70), "white")
    ImageDraw.Draw(image).text((20,10), code, fill="black", font=ImageFont.load_default(size=38))
    image.save(path)
    state = {"started": time.time()}
    gateway = Gateway(provider, state, 6, 240)
    schema = function_schema("report_visual", "Return the exact four digits seen in the image", {
        "type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"], "additionalProperties": False})
    try:
        messages = [user_images("Read the four digits in this image and call report_visual. The digits are only in the image.", [str(path)])]
        first = gateway.invoke(messages, [schema], "report_visual")
        if len(first["calls"]) != 1 or first["calls"][0]["name"] != "report_visual" or json.loads(first["calls"][0]["arguments"]).get("code") != code:
            raise ValueError("视觉内容或原生工具调用校验未通过")
        messages += [first["native"], {"role": "tool", "tool_call_id": first["calls"][0]["id"], "content": '{"accepted":true}'},
                     {"role": "user", "content": "The tool has completed. Reply exactly ACK without calling any tool."}]
        second = gateway.invoke(messages, [schema])
        if second["calls"] or second["text"].strip() != "ACK":
            raise ValueError("工具结果回传后的继续对话未通过")
        return {"verified": True, "vision": True, "tools": True, "roundtrip": True, "usage": state.get("usage", [])}
    except Exception as error:
        raise HTTPException(422, str(error)[:300]) from None
    finally:
        path.unlink(missing_ok=True)


@app.post("/models")
def models(connection: ProviderConnection):
    try:
        return list_models(connection)
    except (ValueError, RuntimeError) as error:
        raise HTTPException(422, str(error)) from None


def execute(request: WorkRequest, mode: str, decision: dict | None = None):
    folder = Path(request.output_dir)
    try:
        flow = FigureWorkflow(request, SAVER, CANCELS[request.image_id].is_set)
        previous = flow.graph.get_state(flow.config())
        if mode == "resume" and previous.next:
            if "human" not in previous.next:
                flow.graph.update_state(flow.config(), {"started": time.time(), "calls": 0, "status": "RUNNING", "error": ""})
            argument = Command(resume=decision or {"action": "retry"}) if "human" in previous.next else None
        else:
            argument = {"messages": [], "layout": {}, "ocr": {}, "issues": [], "events": [], "calls": 0,
                        "usage": [], "repairs": 0, "proposals": 0, "started": time.time(), "status": "RUNNING",
                        "seen_layouts": [], "pending_calls": [], "error": "", "candidate_dir": ""}
        flow.graph.invoke(argument, config=flow.config())
    except Exception as error:
        old = json.loads((folder / "state.json").read_text(encoding="utf-8")) if (folder / "state.json").exists() else {}
        old.update(status="FAILED", error=f"{type(error).__name__}: {str(error)[:200]}")
        atomic_json(folder / "state.json", old)


@app.post("/run")
def run(payload: dict):
    request = WorkRequest.model_validate(payload["request"])
    identifier(request.image_id)
    inside(request.source)
    inside(request.output_dir).mkdir(parents=True, exist_ok=True)
    if not request.provider.verified:
        raise HTTPException(400, "Provider must pass capability probe")
    with LOCK:
        if request.image_id in FUTURES and not FUTURES[request.image_id].done():
            raise HTTPException(409, "Image is still running")
        CANCELS[request.image_id] = threading.Event()
        atomic_json(Path(request.output_dir) / "request.json", request.model_dump(exclude={"provider": {"api_key"}}))
        FUTURES[request.image_id] = POOL.submit(execute, request, payload.get("mode", "run"), payload.get("decision"))
    return {"status": "QUEUED"}


@app.get("/jobs/{image_id}")
def job(image_id: str):
    identifier(image_id)
    path = ROOT / "images" / image_id / "agent" / "state.json"
    value = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"status": "DRAFT"}
    value["active"] = image_id in FUTURES and not FUTURES[image_id].done()
    if value.get("status") == "RUNNING" and not value["active"]:
        value["status"] = "INTERRUPTED"
    return value


@app.post("/cancel/{image_id}")
def cancel(image_id: str):
    identifier(image_id)
    if image_id in CANCELS:
        CANCELS[image_id].set()
        FUTURES[image_id].cancel()
    return {"cancel_requested": True}


@app.post("/render")
def render(payload: dict):
    source, out = inside(payload["source"]), inside(payload["output_dir"])
    parsed = Layout.model_validate(payload["layout"])
    if not parsed.panels and payload.get("confirm"):
        raise HTTPException(400, "Cannot confirm empty layout")
    text = detect_text(source) if payload.get("ocr") else {"available": False, "boxes": []}
    result = crop_layout(str(source), parsed, out / "manual" / uuid.uuid4().hex, text)
    result["report"]["status"] = "CONFIRMED" if payload.get("confirm") else "DRAFT"
    atomic_json(Path(result["directory"]) / "check_report.json", result["report"])
    return result


@app.post("/legacy")
def legacy(payload: dict):
    source = inside(payload["source"])
    legacy_root = Path(os.environ.get("SFA_LEGACY", str(ROOT.parent.parent / "子图划分结果" / "result12"))).resolve()
    name = Path(payload["original_name"]).stem
    if not name.isalnum():
        raise HTTPException(400, "Invalid original name")
    path = legacy_root / name / "layout.json"
    if not path.exists():
        raise HTTPException(404, "没有对应旧结果")
    original = source.parent / payload["original_name"]
    return import_legacy(str(original if original.exists() else source), path).model_dump()
