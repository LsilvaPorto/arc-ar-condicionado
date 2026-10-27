import pytest

from arc.climate import AirConditioner, build_parameters
from arc.names import resolve

TEMP = {"typology": "range", "minimumValue": "16", "maximumValue": "30", "incrementValue": "1", "defaultValue": "22"}
VERT = {"typology": "enum", "enumValues": [2, 4, 5, 6, 8], "defaultValue": "5"}
SPECS = {
    "tempSel": TEMP,
    "windDirectionVertical": VERT,
    "machMode": {"typology": "fixed", "fixedValue": "1"},
    "windSpeed": {"typology": "enum", "enumValues": [1, 2, 3, 5], "defaultValue": "5"},
}


def test_fixos_estado_e_overrides():
    state = {"tempSel": "24.00", "windDirectionVertical": "0", "machMode": "4", "windSpeed": "3"}
    params = build_parameters(SPECS, state, {"windSpeed": "1"})
    assert params == {
        "tempSel": "24",  # estado atual, normalizado
        "windDirectionVertical": "5",  # estado "0" fora do enum -> default
        "machMode": "1",  # fixo vence o estado
        "windSpeed": "1",  # override
    }


@pytest.mark.parametrize("temp", [15, 31, 22.5])
def test_temperatura_invalida(temp):
    with pytest.raises(ValueError, match="16–30"):
        build_parameters(SPECS, {}, {"tempSel": temp})


def ac(name, mac, state=None):
    catalog = {
        "startProgram": {"PROGRAMS.AC.IOT_COOL": {"parameters": SPECS, "ancillaryParameters": {"remoteVisible": {"typology": "fixed", "fixedValue": "1"}}}},
        "stopProgram": {"PROGRAMS.STOP": {"parameters": {"onOffStatus": {"typology": "fixed", "fixedValue": "0"}}}},
        "options": {},
    }
    shadow = {k: {"parNewVal": v} for k, v in (state or {}).items()}
    return AirConditioner(
        {"macAddress": mac, "nickName": name, "applianceTypeName": "AC"},
        catalog,
        {"shadow": {"parameters": shadow}, "lastConnEvent": {"category": "CONNECTED"}},
    )


def test_ligar_no_frio_monta_programa():
    payload = ac("Ar Quarto", "m1").turn_on("frio", 20)
    assert payload["commandName"] == "startProgram"
    assert payload["programName"] == "PROGRAMS.AC.IOT_COOL"
    assert payload["parameters"]["tempSel"] == "20"
    assert payload["ancillaryParameters"] == {"remoteVisible": "1"}


def test_desligar():
    payload = ac("Ar Quarto", "m1").turn_off()
    assert payload["commandName"] == "stopProgram"
    assert payload["parameters"] == {"onOffStatus": "0"}


def test_resolve_nomes():
    devices = [ac("Ar Quarto", "m1"), ac("Ar Escritorio", "m2"), ac(None, "m3")]
    devices[2].appliance["modelName"] = "AS25RBAHRA-3"
    assert [d.mac for d in resolve("quarto", devices, {})] == ["m1"]
    assert [d.mac for d in resolve("Escritório", devices, {})] == ["m2"]
    assert [d.mac for d in resolve("sala", devices, {"sala": "m3"})] == ["m3"]
    assert len(resolve("tudo", devices, {})) == 3
    with pytest.raises(LookupError):
        resolve("cozinha", devices, {})


def test_regras_multisplit_bloqueiam_autolimpeza():
    clean = {"typology": "range", "minimumValue": "0", "maximumValue": "1", "incrementValue": "1"}
    rules = {"selfCleaningStatus": {"$installationType": {"1toN": {"typology": "fixed", "fixedValue": "0"}}}}
    definition = {
        "parameters": {"selfCleaningStatus": clean, "tempSel": TEMP},
        "ancillaryParameters": {"programRules": {"typology": "fixed", "fixedValue": rules}},
    }
    catalog = {"settings": {"setParameters": definition}}
    split = ac("Quarto", "m1"); split.catalog = catalog; split.appliance["unitConfiguration"] = "1to1"
    multi = ac("Sala", "m2"); multi.catalog = catalog; multi.appliance["unitConfiguration"] = "1to2"

    assert "selfCleaningStatus" in split.adjustable()
    assert "selfCleaningStatus" not in multi.adjustable()
    assert multi.apply({})["parameters"]["selfCleaningStatus"] == "0"
    with pytest.raises(ValueError):
        multi.apply({"selfCleaningStatus": "1"})


def test_regra_eco_fixa_temperatura_no_frio():
    from arc.climate import apply_rules

    rules = {"tempSel": {"ecoMode": {"1": {"machMode": {"1": {"typology": "fixed", "fixedValue": "26"}}}}},
             "windSpeed": {"ecoMode": {"1": {"machMode": {"1|4": {"typology": "enum", "enumValues": "1|2|3|5"}}}}}}
    specs = {"tempSel": TEMP, "windSpeed": {"typology": "enum", "enumValues": [1, 2, 3, 5]}}
    assert apply_rules(specs, rules, {"ecoMode": "0", "machMode": "1"}) == specs
    eco = apply_rules(specs, rules, {"ecoMode": "1", "machMode": "1"})
    assert eco["tempSel"] == {"typology": "fixed", "fixedValue": "26"}
    assert eco["windSpeed"]["enumValues"] == ["1", "2", "3", "5"]


R01 = {"typology": "range", "minimumValue": "0", "maximumValue": "1", "incrementValue": "1"}
FULL = {**SPECS, "rapidMode": R01, "muteStatus": R01, "screenDisplayStatus": R01}


def ac_full(state):
    unit = ac("Escritorio", "m9", state)
    unit.catalog = {
        "startProgram": {"PROGRAMS.AC.IOT_COOL": {"parameters": FULL},
                         "PROGRAMS.AC.IOT_DRY": {"parameters": {**FULL, "rapidMode": {"typology": "fixed", "fixedValue": "0"}}}},
        "settings": {"setParameters": {"parameters": FULL}},
    }
    return unit


def test_turbo_desliga_silencioso_e_trava_ventilador():
    p = ac_full({"rapidMode": "0", "muteStatus": "1", "windSpeed": "5"}).apply({"rapidMode": "1"})["parameters"]
    assert (p["rapidMode"], p["muteStatus"], p["windSpeed"]) == ("1", "0", "5")


def test_silencioso_desliga_turbo():
    p = ac_full({"rapidMode": "1", "muteStatus": "0", "windSpeed": "5"}).apply({"muteStatus": "1"})["parameters"]
    assert (p["rapidMode"], p["muteStatus"], p["windSpeed"]) == ("0", "1", "5")
    with pytest.raises(ValueError, match="juntos"):
        ac_full({}).apply({"muteStatus": "1", "rapidMode": "1"})


def test_ventilador_travado_com_turbo():
    with pytest.raises(ValueError, match="automático"):
        ac_full({"rapidMode": "1"}).apply({"windSpeed": "1"})


def test_seco_ignora_turbo_do_estado():
    p = ac_full({"rapidMode": "1", "windSpeed": "5"}).start("IOT_DRY", {"windSpeed": "3"})["parameters"]
    assert (p["rapidMode"], p["windSpeed"]) == ("0", "3")


def test_preferencia_de_luz_vai_ao_ligar_mas_nao_no_ajuste():
    unit = ac_full({"screenDisplayStatus": "1"})
    unit.preferences = {"screenDisplayStatus": "0"}
    assert unit.start("IOT_COOL")["parameters"]["screenDisplayStatus"] == "0"
    assert unit.start("IOT_COOL", {"screenDisplayStatus": "1"})["parameters"]["screenDisplayStatus"] == "1"
