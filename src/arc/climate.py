"""Controle de ar-condicionado sobre o catálogo de comandos do hOn.

Cada comando do catálogo (/commands/v1/retrieve) declara seus parâmetros como
fixed / range / enum. Para enviar, montamos todos eles: valor fixo, ou o que o
usuário pediu, ou o estado atual do aparelho (shadow), ou o default.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from arc.hon import APP_VERSION, DEVICE_MODEL, OS, OS_VERSION, HonClient, HonError, shadow_parameters

MODES = {"auto": "0", "frio": "1", "seco": "2", "calor": "4", "ventilar": "6"}
MODE_PROGRAMS = {"auto": "IOT_AUTO", "frio": "IOT_COOL", "seco": "IOT_DRY", "calor": "IOT_HEAT", "ventilar": "IOT_FAN"}
FAN = {"alto": "1", "medio": "2", "baixo": "3", "auto": "5"}


def _label(table: dict[str, str], value: str | None) -> str:
    return next((k for k, v in table.items() if v == value), value or "?")


def _num(value: Any) -> float:
    return float(str(value).replace(",", "."))


def _fmt(value: float) -> str:
    return str(int(value)) if value == int(value) else str(value)


def _valid(spec: dict[str, Any], value: Any) -> bool:
    if value is None:
        return False
    match spec.get("typology"):
        case "range":
            try:
                v, lo, hi, step = (_num(x) for x in (value, spec["minimumValue"], spec["maximumValue"], spec["incrementValue"]))
            except (KeyError, ValueError):
                return False
            return lo <= v <= hi and (v - lo) % step == 0
        case "enum":
            return str(value) in [str(x) for x in spec.get("enumValues", [])]
    return False


def _normalize(spec: dict[str, Any], value: Any) -> str:
    return _fmt(_num(value)) if spec.get("typology") == "range" else str(value)


def build_parameters(specs: dict[str, Any], state: dict[str, Any], overrides: dict[str, Any]) -> dict[str, str]:
    """Monta os parâmetros de um comando. Overrides inválidos geram ValueError."""
    result = {}
    for key, spec in specs.items():
        if spec.get("typology") == "fixed":
            fixed = str(spec.get("fixedValue", "0"))
            if key in overrides and _normalize({}, overrides[key]) != fixed:
                raise ValueError(f"{key} não pode ser alterado neste modo (fixo em {fixed})")
            result[key] = fixed
            continue
        if key in overrides:
            if not _valid(spec, overrides[key]):
                allowed = (
                    f"{spec.get('minimumValue')}–{spec.get('maximumValue')}"
                    if spec.get("typology") == "range"
                    else ", ".join(str(x) for x in spec.get("enumValues", []))
                )
                raise ValueError(f"Valor inválido para {key}: {overrides[key]} (permitido: {allowed})")
            result[key] = _normalize(spec, overrides[key])
        elif _valid(spec, state.get(key)):
            result[key] = _normalize(spec, state[key])
        elif spec.get("defaultValue") is not None:
            result[key] = _normalize(spec, spec["defaultValue"])
        elif spec.get("typology") == "enum" and spec.get("enumValues"):
            result[key] = str(spec["enumValues"][0])
        else:
            result[key] = str(spec.get("minimumValue", "0"))
    return result


def build_ancillary(specs: dict[str, Any]) -> dict[str, Any]:
    # Mesmo comportamento da integração de referência, que funciona hoje.
    result = {}
    for key, spec in specs.items():
        if spec.get("typology") == "fixed":
            result[key] = spec.get("fixedValue")
        else:
            result[key] = spec.get("defaultValue") or "0"
    return result


def observed_rules(specs: dict[str, Any], state: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """Regras que o aparelho aplica sozinho e o catálogo não documenta.

    Validadas no Escritório (AS35RBAHRA) em 2026-10-27:
    - turbo ligado desliga o silencioso e trava o ventilador em auto;
    - silencioso é recusado com o turbo ligado, e também trava o ventilador em auto.
    Padronizamos como "liga um, desliga o outro": ligar o silencioso já manda o
    turbo desligado no mesmo comando.
    """

    def value(key: str) -> str:
        spec = specs.get(key)
        if spec is None:
            return "0"
        if spec.get("typology") == "fixed":
            return str(spec.get("fixedValue"))
        return str(overrides.get(key, state.get(key, "0")))

    def settable(key: str) -> bool:
        return key in specs and specs[key].get("typology") != "fixed"

    result = dict(overrides)
    if str(overrides.get("rapidMode")) == "1" and str(overrides.get("muteStatus")) == "1":
        raise ValueError("Turbo e Silencioso não podem ficar ligados juntos")
    if str(overrides.get("muteStatus")) == "1" and settable("rapidMode"):
        result["rapidMode"] = "0"
    if str(overrides.get("rapidMode")) == "1" and settable("muteStatus"):
        result["muteStatus"] = "0"
    mute = str(result.get("muteStatus", value("muteStatus")))
    if (value("rapidMode") == "1" or mute == "1") and settable("windSpeed"):
        if "windSpeed" in overrides and str(overrides["windSpeed"]) != "5":
            raise ValueError("Com Turbo ou Silencioso ligado o ventilador fica no automático")
        result["windSpeed"] = "5"
    return result


def _matches(condition: str, value: Any) -> bool:
    return str(value) in condition.split("|")


def _rule_spec(spec: dict[str, Any]) -> dict[str, Any]:
    spec = dict(spec)
    if isinstance(spec.get("enumValues"), str):
        spec["enumValues"] = spec["enumValues"].split("|")
    return spec


def apply_rules(specs: dict[str, Any], rules: dict[str, Any] | None, values: dict[str, Any]) -> dict[str, Any]:
    """Aplica as `programRules` do catálogo: restrições que dependem de outros valores.

    Formato: {parametro: {condição: {valor|valor: <spec> ou {outra condição: ...}}}}.
    Ex.: em multi-split ($installationType=1toN) a autolimpeza vira fixed 0;
    com ecoMode=1 no frio, a temperatura vira fixed 26.
    """
    if not rules:
        return specs
    result = dict(specs)

    def resolve(node: dict[str, Any]) -> dict[str, Any] | None:
        if "typology" in node:
            return node
        for condition_key, branches in node.items():
            for condition, child in branches.items():
                if _matches(condition, values.get(condition_key)):
                    found = resolve(child)
                    if found:
                        return found
        return None

    for key, node in rules.items():
        if key in result and (spec := resolve(node)):
            result[key] = _rule_spec(spec)
    return result


@dataclass
class AirConditioner:
    appliance: dict[str, Any]
    catalog: dict[str, Any]
    context: dict[str, Any]
    # Preferências guardadas pelo servidor (ex.: luz do painel), aplicadas ao ligar.
    preferences: dict[str, str] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.appliance.get("nickName") or self.appliance.get("modelName") or self.mac

    @property
    def mac(self) -> str:
        return self.appliance["macAddress"]

    @property
    def state(self) -> dict[str, Any]:
        return shadow_parameters(self.context)

    @property
    def online(self) -> bool:
        return (self.context.get("lastConnEvent") or {}).get("category") == "CONNECTED"

    @property
    def installation_type(self) -> str:
        """1to1 = split simples; 1toN = multi-split (várias internas numa externa)."""
        config = self.appliance.get("unitConfiguration") or "1to1"
        return "1to1" if config == "1to1" else "1toN"

    def _specs(self, definition: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
        ancillary = definition.get("ancillaryParameters", {})
        rules = (ancillary.get("programRules") or {}).get("fixedValue")
        values = {
            "$installationType": self.installation_type,
            **{k: v for k, v in build_ancillary(ancillary).items() if k != "programRules"},
            **self.state,
            **overrides,
        }
        return apply_rules(definition.get("parameters", {}), rules, values)

    @property
    def is_on(self) -> bool:
        return self.state.get("onOffStatus") == "1"

    def summary(self) -> dict[str, Any]:
        s = self.state
        return {
            "nome": self.name,
            "online": self.online,
            "ligado": self.is_on,
            "modo": _label(MODES, s.get("machMode")),
            "alvo": _num(s["tempSel"]) if s.get("tempSel") else None,
            "ambiente": _num(s["tempIndoor"]) if s.get("tempIndoor") else None,
            "externa": _num(s["tempOutdoor"]) if s.get("tempOutdoor") else None,
            "ventilador": _label(FAN, s.get("windSpeed")),
        }

    # --- montagem de comandos ---------------------------------------------

    def _definition(self, command: str, program: str | None = None) -> tuple[str | None, dict[str, Any]]:
        entry = self.catalog.get(command)
        if not entry:
            raise HonError(f"{self.name} não tem o comando {command}")
        if command == "settings":
            return None, entry["setParameters"]
        if program is None:
            # stopProgram tem um único programa
            program, definition = next(iter(entry.items()))
            return program, definition
        full = f"PROGRAMS.AC.{program}"
        if full not in entry:
            raise HonError(f"{self.name} não suporta {program}")
        return full, entry[full]

    def build(self, command: str, overrides: dict[str, Any], program: str | None = None) -> dict[str, Any]:
        program_name, definition = self._definition(command, program)
        if command in ("startProgram", "settings"):
            specs = self._specs(definition, overrides)
            if command == "startProgram":
                # A luz volta acesa sempre que o aparelho desliga; ao ligar, reenviamos a preferência.
                prefs = {k: v for k, v in self.preferences.items() if k in specs and specs[k].get("typology") != "fixed"}
                overrides = {**prefs, **overrides}
            overrides = observed_rules(specs, self.state, overrides)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        payload: dict[str, Any] = {
            "macAddress": self.mac,
            "timestamp": now,
            "commandName": command,
            "transactionId": f"{self.mac}_{now}",
            "applianceOptions": self.catalog.get("options") or {},
            "device": {
                "mobileId": "arc-cli",
                "mobileOs": OS,
                "osVersion": OS_VERSION,
                "appVersion": APP_VERSION,
                "deviceModel": DEVICE_MODEL,
            },
            "attributes": {"channel": "mobileApp", "origin": "standardProgram", "energyLabel": "0"},
            "ancillaryParameters": build_ancillary(definition.get("ancillaryParameters", {})),
            "parameters": build_parameters(self._specs(definition, overrides), self.state, overrides),
            "applianceType": self.appliance["applianceTypeName"],
        }
        if command == "startProgram" and program_name:
            payload["programName"] = program_name
            payload["attributes"]["prStr"] = program_name
        return payload

    @property
    def programs(self) -> list[str]:
        return [p.removeprefix("PROGRAMS.AC.") for p in self.catalog.get("startProgram", {})]

    def adjustable(self, program: str | None = None) -> dict[str, dict[str, Any]]:
        """Parâmetros que o usuário pode mudar num programa (ou no settings, se None)."""
        _, definition = self._definition("startProgram", program) if program else self._definition("settings")
        return {k: v for k, v in self._specs(definition, {}).items() if v.get("typology") != "fixed"}

    def start(self, program: str, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        """Liga com um programa (IOT_COOL, IOT_10_HEATING, ...). Overrides fora do programa são ignorados."""
        allowed = self.adjustable(program)
        return self.build("startProgram", {k: v for k, v in (overrides or {}).items() if k in allowed}, program)

    def apply(self, overrides: dict[str, Any]) -> dict[str, Any]:
        """Muda parâmetros com o aparelho ligado (settings força onOffStatus=1)."""
        return self.build("settings", overrides)

    def turn_on(self, mode: str | None = None, temp: float | None = None, fan: str | None = None) -> dict[str, Any]:
        overrides: dict[str, Any] = {}
        if temp is not None:
            overrides["tempSel"] = temp
        if fan is not None:
            overrides["windSpeed"] = FAN[fan]
        program = MODE_PROGRAMS[mode] if mode else "IOT_SIMPLE_START"
        return self.build("startProgram", overrides, program)

    def turn_off(self) -> dict[str, Any]:
        return self.build("stopProgram", {})

    def adjust(self, mode: str | None = None, temp: float | None = None, fan: str | None = None) -> dict[str, Any]:
        """Muda configurações com o aparelho ligado (o comando settings força onOffStatus=1)."""
        overrides: dict[str, Any] = {}
        if mode is not None:
            overrides["machMode"] = MODES[mode]
        if temp is not None:
            overrides["tempSel"] = temp
        if fan is not None:
            overrides["windSpeed"] = FAN[fan]
        return self.build("settings", overrides)


def load_all(client: HonClient, catalogs: dict[str, dict[str, Any]] | None = None) -> list[AirConditioner]:
    """Carrega os aparelhos. `catalogs` (por MAC) evita buscar o catálogo, que não muda, a cada chamada."""
    from arc.hon import CLIMATE_TYPE_ID

    from concurrent.futures import ThreadPoolExecutor

    catalogs = {} if catalogs is None else catalogs
    appliances = [a for a in client.appliances() if a.get("applianceTypeId") == CLIMATE_TYPE_ID]

    # Cada consulta leva ~1 s na nuvem; em paralelo o total fica perto de uma só.
    def load(a: dict[str, Any]) -> AirConditioner:
        if a["macAddress"] not in catalogs:
            catalogs[a["macAddress"]] = client.commands(a)
        return AirConditioner(a, catalogs[a["macAddress"]], client.context(a))

    with ThreadPoolExecutor(max_workers=max(1, len(appliances))) as pool:
        return list(pool.map(load, appliances))
