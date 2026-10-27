import copy
import threading

from fastapi import FastAPI
from fastapi.testclient import TestClient

from arc.climate import AirConditioner
from arc.web import build_router

RANGE01 = {"typology": "range", "minimumValue": "0", "maximumValue": "1", "incrementValue": "1"}
PARAMS = {
    "tempSel": {"typology": "range", "minimumValue": "16", "maximumValue": "30", "incrementValue": "1", "defaultValue": "22"},
    "windSpeed": {"typology": "enum", "enumValues": [1, 2, 3, 5], "defaultValue": "5"},
    "rapidMode": RANGE01,
    "onOffStatus": {"typology": "fixed", "fixedValue": "1"},
}


def program(mach):
    return {"parameters": {**PARAMS, "machMode": {"typology": "fixed", "fixedValue": mach}}, "ancillaryParameters": {}}


CATALOG = {
    "startProgram": {"PROGRAMS.AC.IOT_COOL": program("1"), "PROGRAMS.AC.IOT_HEAT": program("4"), "PROGRAMS.AC.IOT_SELF_CLEAN": program("1")},
    "settings": {"setParameters": {"parameters": {**PARAMS, "machMode": {"typology": "enum", "enumValues": [0, 1, 2, 4, 6]}}}},
    "stopProgram": {"PROGRAMS.STOP": {"parameters": {"onOffStatus": {"typology": "fixed", "fixedValue": "0"}}}},
}


class FakeService:
    aliases = {}

    def __init__(self, on):
        state = {"onOffStatus": "1" if on else "0", "machMode": "1", "tempSel": "24.00", "windSpeed": "3", "tempIndoor": "20.50"}
        self.ac = AirConditioner(
            {"macAddress": "m1", "nickName": "Ar Quarto", "applianceTypeName": "AC", "modelName": "AS25"},
            copy.deepcopy(CATALOG),
            {"shadow": {"parameters": {k: {"parNewVal": v} for k, v in state.items()}}, "lastConnEvent": {"category": "CONNECTED"}},
        )
        self.sent = []

    def devices(self):
        return [self.ac]

    def device(self, mac):
        return self.ac if mac == self.ac.mac else None

    def remember(self, ac, key, value):
        ac.preferences = {**ac.preferences, key: str(value)}

    def send(self, ac, payload):
        self.sent.append(payload)
        for k, v in payload["parameters"].items():
            ac.context["shadow"]["parameters"][k] = {"parNewVal": v}


def client(service, schedules=None):
    app = FastAPI()
    app.include_router(build_router(lambda: service, threading.Lock(), schedules))
    return TestClient(app)


def test_lista_expoe_modos_e_capacidades():
    data = client(FakeService(on=True)).get("/api/aparelhos").json()
    d = data[0]
    assert d["programa"] == "IOT_COOL" and d["ligado"] is True
    assert set(d["programas"]) == {"IOT_COOL", "IOT_HEAT"}  # limpeza não vira modo
    assert d["programas"]["IOT_COOL"]["tempSel"] == {"tipo": "range", "min": 16, "max": 30, "passo": 1}


def test_ajustar_usa_settings_e_trocar_modo_usa_start():
    service = FakeService(on=True)
    c = client(service)
    r = c.post("/api/aparelhos/m1/ajustar", json={"parametros": {"tempSel": 26}})
    assert r.status_code == 200 and r.json()["estado"]["tempSel"] == "26"
    assert service.sent[-1]["commandName"] == "settings"

    r = c.post("/api/aparelhos/m1/ajustar", json={"programa": "IOT_HEAT", "parametros": {}})
    assert service.sent[-1]["programName"] == "PROGRAMS.AC.IOT_HEAT"
    assert service.sent[-1]["parameters"]["tempSel"] == "26"  # mantém o estado
    assert r.json()["programa"] == "IOT_HEAT"


def test_ligar_com_rascunho_e_validacoes():
    service = FakeService(on=False)
    c = client(service)
    assert c.post("/api/aparelhos/m1/ajustar", json={"parametros": {"tempSel": 22}}).status_code == 400
    assert c.post("/api/aparelhos/m1/ligar", json={"parametros": {"tempSel": 99}}).status_code == 400
    assert c.post("/api/aparelhos/m1/ligar", json={"parametros": {"lockStatus": "1"}}).status_code == 400
    r = c.post("/api/aparelhos/m1/ligar", json={"programa": "IOT_COOL", "parametros": {"tempSel": 21, "windSpeed": "1"}})
    assert r.status_code == 200 and r.json()["ligado"] is True
    assert service.sent[-1]["parameters"]["windSpeed"] == "1"
    assert c.post("/api/aparelhos/m1/desligar").json()["ligado"] is False
    assert c.post("/api/aparelhos/xx/desligar").status_code == 404


def test_luz_usa_valor_lembrado_e_nao_o_da_nuvem():
    service = FakeService(on=True)
    light = {"typology": "range", "minimumValue": "0", "maximumValue": "1", "incrementValue": "1"}
    service.ac.catalog["settings"]["setParameters"]["parameters"]["screenDisplayStatus"] = light
    service.ac.catalog["startProgram"]["PROGRAMS.AC.IOT_COOL"]["parameters"]["screenDisplayStatus"] = light
    remembered = {}
    service.remember = lambda ac, k, v: remembered.update({k: v}) or setattr(ac, "preferences", {**ac.preferences, k: str(v)})
    c = client(service)
    c.post("/api/aparelhos/m1/ajustar", json={"parametros": {"screenDisplayStatus": "0"}})
    # a nuvem continua dizendo "acesa", como no aparelho real
    service.ac.context["shadow"]["parameters"]["screenDisplayStatus"] = {"parNewVal": "1"}
    assert remembered == {"screenDisplayStatus": "0"}
    assert c.get("/api/aparelhos").json()[0]["estado"]["screenDisplayStatus"] == "0"


def test_nao_permite_funcao_fora_do_modo():
    service = FakeService(on=True)
    # No "seco" deste catálogo o turbo é fixo (como no aparelho real).
    dry = program("2")
    dry["parameters"]["rapidMode"] = {"typology": "fixed", "fixedValue": "0"}
    service.ac.catalog["startProgram"]["PROGRAMS.AC.IOT_DRY"] = dry
    service.ac.context["shadow"]["parameters"]["machMode"] = {"parNewVal": "2"}
    c = client(service)
    r = c.post("/api/aparelhos/m1/ajustar", json={"parametros": {"rapidMode": "1"}})
    assert r.status_code == 400 and "Turbo não está disponível no modo Seco" in r.json()["detail"]
    assert service.sent == []
    assert "rapidMode" not in c.get("/api/aparelhos").json()[0]["programas"]["IOT_DRY"]
    # trocar para frio já pedindo turbo é permitido
    assert c.post("/api/aparelhos/m1/ajustar", json={"programa": "IOT_COOL", "parametros": {"rapidMode": "1"}}).status_code == 200


def test_agendamentos_api(tmp_path):
    from arc.schedule import ScheduleStore

    service = FakeService(on=False)
    c = client(service, ScheduleStore(tmp_path / "agenda.json"))
    base = {"aparelhos": ["m1"], "inicio": "22:30", "fim": "07:00", "programa": "IOT_COOL",
            "parametros": {"tempSel": 23, "rapidMode": "0"}}

    assert c.post("/api/agendamentos", json={**base, "aparelhos": ["xx"]}).status_code == 400
    assert c.post("/api/agendamentos", json={**base, "parametros": {"lockStatus": "1"}}).status_code == 400
    r = c.post("/api/agendamentos", json={**base, "programa": "IOT_DRY"})
    assert r.status_code == 400  # o catálogo de teste não tem IOT_DRY

    r = c.post("/api/agendamentos", json=base)
    assert r.status_code == 200
    created = r.json()
    assert created["descricao"] == "Todo dia · 22:30–07:00" and created["proximo"]

    lista = c.get("/api/agendamentos").json()
    assert [a["id"] for a in lista["agendamentos"]] == [created["id"]]

    r = c.post(f"/api/agendamentos/{created['id']}/testar?acao=ligar")
    assert r.json()["resultado"] == {"m1": "ok"}
    assert service.sent[-1]["programName"] == "PROGRAMS.AC.IOT_COOL"
    assert service.sent[-1]["parameters"]["tempSel"] == "23"

    r = c.put(f"/api/agendamentos/{created['id']}", json={**base, "ativo": False})
    assert r.json()["ativo"] is False and r.json()["proximo"] is None
    assert c.get("/api/agendamentos").json()["historico"] == []  # "testar" não entra no histórico
    store = ScheduleStore(tmp_path / "agenda.json")
    store.record("k1", {"acao": "ligar"})
    assert len(c.get("/api/agendamentos").json()["historico"]) == 1
    assert c.delete("/api/agendamentos/historico").status_code == 200
    assert c.get("/api/agendamentos").json()["historico"] == []
    assert "k1" in store.done_keys()  # continua sabendo o que já rodou
    assert c.delete(f"/api/agendamentos/{created['id']}").status_code == 200
    assert c.get("/api/agendamentos").json()["agendamentos"] == []
