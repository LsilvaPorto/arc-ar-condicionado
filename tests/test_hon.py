import json

import httpx
import pytest

from arc.hon import API_URL, HonAuthError, HonClient, shadow_parameters


def make_client(handler):
    return HonClient("eu@example.com", "senha", http=httpx.Client(base_url=API_URL, transport=httpx.MockTransport(handler)))


def test_login_e_lista_usa_tokens_nos_headers():
    seen = {}

    def handler(req: httpx.Request):
        if req.url.path == "/ciam/authorize":
            assert req.url.params["username"] == "eu@example.com"
            assert req.url.params["code_challenge"]
            return httpx.Response(200, json={"session_id": "S1"})
        if req.url.path == "/ciam/token":
            body = json.loads(req.content)
            assert body["session_id"] == "S1" and body["code_verifier"]
            return httpx.Response(200, json={"tokens": {"cognito_token": "C", "id_token": "I"}})
        if req.url.path == "/unified-api/v1/view/appliance-list":
            seen["headers"] = req.headers
            return httpx.Response(200, json={"modules": {"applianceList": {"payload": {"appliances": [
                {"macAddress": "aa", "applianceTypeId": 11, "nickName": "Quarto"},
                {"nickName": "sem mac"},
            ]}}}})
        return httpx.Response(404)

    with make_client(handler) as client:
        appliances = client.appliances()

    assert [a["nickName"] for a in appliances] == ["Quarto"]
    assert seen["headers"]["cognito-token"] == "C"
    assert seen["headers"]["id-token"] == "I"


def test_login_recusado_sem_session_id():
    with make_client(lambda req: httpx.Response(200, json={})) as client:
        with pytest.raises(HonAuthError, match="session_id"):
            client.authorize()


def test_shadow_parameters():
    ctx = {"shadow": {"parameters": {"tempSel": {"parNewVal": "22"}, "onOffStatus": {"parNewVal": "1"}}}}
    assert shadow_parameters(ctx) == {"tempSel": "22", "onOffStatus": "1"}
    assert shadow_parameters({}) == {}
