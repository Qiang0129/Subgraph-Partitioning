"""Two explicit wire protocols; secrets never enter workflow checkpoints."""
import copy
import json
import time
from urllib.parse import urlparse

import httpx

from .models import Provider
from .vision import data_image, read_image


class BudgetExceeded(RuntimeError):
    pass


class Gateway:
    def __init__(self, provider: Provider, state: dict, max_calls=24, max_seconds=600):
        self.provider, self.state = provider, state
        self.max_calls, self.max_seconds = max_calls, max_seconds

    def endpoint(self):
        base = self.provider.base_url.rstrip("/")
        parsed = urlparse(base)
        if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("Provider URL must be HTTP(S), without embedded credentials")
        suffix = "/chat/completions" if self.provider.protocol == "chat" else "/responses"
        if base.endswith(("/chat/completions", "/responses")):
            if not base.endswith(suffix):
                raise ValueError("Endpoint does not match selected protocol")
            return base
        return base + suffix

    def post(self, payload):
        for retry in range(3):
            elapsed = time.time() - self.state.setdefault("started", time.time())
            if self.state.get("calls", 0) >= self.max_calls or elapsed >= self.max_seconds:
                raise BudgetExceeded("模型调用次数或总时间已达到本轮上限")
            self.state["calls"] = self.state.get("calls", 0) + 1
            timeout = max(1, min(self.provider.timeout_seconds, self.max_seconds-elapsed))
            try:
                response = httpx.post(self.endpoint(), headers={"Authorization": f"Bearer {self.provider.api_key}"},
                                      json=payload, timeout=timeout)
            except httpx.RequestError:
                if retry == 2:
                    raise RuntimeError("模型网络请求失败，请检查服务地址、代理或网络") from None
                time.sleep(2 ** retry)
                continue
            if response.status_code in (429, 500, 502, 503, 504) and retry < 2:
                time.sleep(2 ** retry)
                continue
            if response.is_error:
                raise RuntimeError(f"模型 HTTP {response.status_code}；请核对供应商配置和协议")
            data = response.json()
            usage = data.get("usage", {})
            self.state.setdefault("usage", []).append(usage)
            return data
        raise RuntimeError("模型请求失败")

    def invoke(self, messages: list[dict], tools: list[dict], force: str | None = None) -> dict:
        history = copy.deepcopy(messages)
        if self.provider.protocol == "chat":
            for message in history:
                if isinstance(message.get("content"), list):
                    for i, part in enumerate(message["content"]):
                        if part.get("type") == "image_ref":
                            url, _ = data_image(read_image(part["path"]))
                            message["content"][i] = {"type": "image_url", "image_url": {"url": url}}
            payload = {"model": self.provider.model, "messages": history, "tools": tools}
            if force:
                payload["tool_choice"] = {"type": "function", "function": {"name": force}}
            raw = self.post(payload)
            item = raw["choices"][0]["message"]
            calls = [{"id": c["id"], "name": c["function"]["name"], "arguments": c["function"]["arguments"]}
                     for c in item.get("tool_calls", [])]
            native = {k: v for k, v in item.items() if k in {"role", "content", "tool_calls"} and v is not None}
            return {"calls": calls, "native": native, "text": item.get("content") or ""}
        native_input = []
        for message in history:
            if message.get("_responses"):
                native_input.extend(message["items"])
            elif message["role"] == "tool":
                native_input.append({"type": "function_call_output", "call_id": message["tool_call_id"], "output": message["content"]})
            else:
                content = message.get("content", "")
                if isinstance(content, str):
                    native_input.append({"role": message["role"], "content": content})
                else:
                    parts = []
                    for part in content:
                        if part["type"] == "image_ref":
                            url, _ = data_image(read_image(part["path"]))
                            parts.append({"type": "input_image", "image_url": url})
                        else:
                            parts.append({"type": "input_text", "text": part["text"]})
                    native_input.append({"role": message["role"], "content": parts})
        payload = {"model": self.provider.model, "input": native_input, "store": False,
                   "tools": [{"type": "function", **t["function"]} for t in tools]}
        if force:
            payload["tool_choice"] = {"type": "function", "name": force}
        raw = self.post(payload)
        output = raw["output"]
        calls = [{"id": c["call_id"], "name": c["name"], "arguments": c["arguments"]}
                 for c in output if c["type"] == "function_call"]
        text = " ".join(c.get("text", "") for item in output if item["type"] == "message" for c in item.get("content", []))
        return {"calls": calls, "native": {"role": "assistant", "_responses": True, "items": output}, "text": text}


def function_schema(name: str, description: str, parameters: dict) -> dict:
    return {"type": "function", "function": {"name": name, "description": description, "parameters": parameters}}


def user_images(text: str, paths: list[str]) -> dict:
    return {"role": "user", "content": [{"type": "text", "text": text}, *[{"type": "image_ref", "path": p} for p in paths]]}
