import json

import httpx
import pytest
from pydantic import ValidationError

from app.gateway import Gateway, list_models
from app.models import Provider, ProviderConnection


@pytest.mark.parametrize("suffix", ["/v1", "/v1/", "/v1/responses", "/v1/chat/completions", "/v1/models"])
def test_list_models_normalizes_endpoint_and_auth(monkeypatch, suffix):
    def get(url, **kwargs):
        assert url == "http://fixture.invalid/v1/models"
        assert kwargs["headers"]["Authorization"] == "Bearer MODEL_LIST_CANARY"
        assert kwargs["follow_redirects"] is False
        return httpx.Response(200, json={"data": [{"id": "z-model"}, {"id": "a-model"}, {"id": "z-model"}, {}, None]})
    monkeypatch.setattr(httpx, "get", get)
    result = list_models(ProviderConnection(base_url="http://fixture.invalid" + suffix, api_key="MODEL_LIST_CANARY"))
    assert result == {"models": ["a-model", "z-model"]}
    assert "CANARY" not in json.dumps(result)


@pytest.mark.parametrize("status", [301, 401, 403, 404, 405, 429, 500])
def test_model_list_http_errors_do_not_echo_provider_body(monkeypatch, status):
    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: httpx.Response(status, text="PRIVATE_KEY_CANARY"))
    with pytest.raises(RuntimeError) as error:
        list_models(ProviderConnection(base_url="http://fixture.invalid/v1"))
    assert "PRIVATE_KEY_CANARY" not in str(error.value)


@pytest.mark.parametrize("payload", [{}, {"data": {}}, {"data": [{"wrong": "id"}]}])
def test_model_list_rejects_malformed_payload(monkeypatch, payload):
    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: httpx.Response(200, json=payload))
    with pytest.raises(RuntimeError):
        list_models(ProviderConnection(base_url="http://fixture.invalid/v1"))


def test_model_list_empty_and_network_error(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: httpx.Response(200, json={"data": []}))
    assert list_models(ProviderConnection(base_url="http://fixture.invalid/v1")) == {"models": []}
    def timeout(*args, **kwargs):
        raise httpx.ConnectTimeout("INTERNAL_REQUEST_CANARY")
    monkeypatch.setattr(httpx, "get", timeout)
    with pytest.raises(RuntimeError) as error:
        list_models(ProviderConnection(base_url="http://fixture.invalid/v1"))
    assert "INTERNAL_REQUEST_CANARY" not in str(error.value)


@pytest.mark.parametrize("url", ["file:///private", "http://user:secret@fixture.invalid/v1", "https://fixture.invalid/v1?key=secret", "http://fixture.invalid:bad/v1", "http://:80/v1"])
def test_model_list_invalid_url_never_requests_network(monkeypatch, url):
    def forbidden(*args, **kwargs):
        raise AssertionError("Network must not be called")
    monkeypatch.setattr(httpx, "get", forbidden)
    with pytest.raises(ValueError):
        list_models(ProviderConnection(base_url=url))


@pytest.mark.parametrize("protocol", ["chat", "responses"])
@pytest.mark.parametrize("effort", ["default", "none", "minimal", "low", "medium", "high", "xhigh", "max"])
def test_reasoning_effort_actual_wire_payload(monkeypatch, protocol, effort):
    requests = []
    def post(self, payload):
        requests.append(payload)
        return {"choices": [{"message": {"role": "assistant", "content": "ok"}}]} if protocol == "chat" else {"output": [{"type": "message", "content": [{"text": "ok"}]}]}
    monkeypatch.setattr(Gateway, "post", post)
    provider = Provider(base_url="http://fixture.invalid/v1", protocol=protocol, model="fixture", reasoning_effort=effort)
    gateway = Gateway(provider, {})
    for _ in range(2):
        assert gateway.invoke([{"role": "user", "content": "test"}], [], force="report")["text"] == "ok"
    for payload in requests:
        if effort == "default":
            assert "reasoning" not in payload and "reasoning_effort" not in payload
        elif protocol == "chat":
            assert payload["reasoning_effort"] == effort and "reasoning" not in payload
        else:
            assert payload["reasoning"] == {"effort": effort} and "reasoning_effort" not in payload


def test_unknown_reasoning_effort_rejected():
    with pytest.raises(ValidationError):
        Provider(base_url="http://fixture.invalid", model="fixture", reasoning_effort="guess")
