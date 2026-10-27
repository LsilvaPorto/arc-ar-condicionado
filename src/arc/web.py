"""API JSON e arquivos estáticos do webapp."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from dataclasses import asdict, fields
from datetime import datetime

from arc.climate import AirConditioner, _num
from arc.hon import HonError
from arc.schedule import Schedule, ScheduleStore, describe_when, next_event
from arc.service import Service, ServiceError, display_name

STATIC_DIR = Path(__file__).parent / "static"

# Programas que viram "modos" na interface, na ordem dos botões.
MODE_PROGRAMS = {
    "IOT_COOL": "1",
    "IOT_HEAT": "4",
    "IOT_DRY": "2",
    "IOT_FAN": "6",
    "IOT_AUTO": "0",
    "IOT_10_HEATING": "4",
}

# A nuvem não reporta mudanças da luz feitas com o aparelho ligado (validado com o
# aparelho na frente): o valor confiável é o último que mandamos.
REMEMBERED = {"screenDisplayStatus"}

# Parâmetros que o webapp mostra/edita.
EDITABLE = {
    "tempSel",
    "windSpeed",
    "windDirectionVertical",
    "windDirectionHorizontal",
    "silentSleepStatus",
    "muteStatus",
    "rapidMode",
    "screenDisplayStatus",
    "selfCleaningStatus",
    "healthMode",
}

STATE_KEYS = EDITABLE | {"onOffStatus", "machMode", "tempIndoor", "tempOutdoor", "10degreeHeatingStatus", "errors", "filterChangeStatusLocal"}


def current_program(state: dict[str, Any]) -> str | None:
    if state.get("10degreeHeatingStatus") == "1":
        return "IOT_10_HEATING"
    return next((p for p, m in MODE_PROGRAMS.items() if m == state.get("machMode") and p != "IOT_10_HEATING"), None)


LABELS = {
    "tempSel": "Temperatura",
    "windSpeed": "Ventilador",
    "windDirectionVertical": "Direção vertical",
    "windDirectionHorizontal": "Direção horizontal",
    "silentSleepStatus": "Modo noturno",
    "muteStatus": "Silencioso",
    "rapidMode": "Turbo",
    "screenDisplayStatus": "Luz do painel",
    "selfCleaningStatus": "Antimofo",
    "healthMode": "Ionizador",
}
MODE_NAMES = {"IOT_COOL": "Frio", "IOT_HEAT": "Calor", "IOT_DRY": "Seco", "IOT_FAN": "Ventilar", "IOT_AUTO": "Auto", "IOT_10_HEATING": "10° anti-gelo"}


def allowed_in(ac: AirConditioner, program: str) -> set[str]:
    """O que pode ser mudado num modo: o programa E o settings precisam aceitar."""
    return set(ac.adjustable(program)) & set(ac.adjustable()) & EDITABLE


def check_allowed(ac: AirConditioner, program: str, params: dict[str, Any]) -> None:
    if program not in ac.programs:
        raise ValueError(f"Este aparelho não tem o modo {MODE_NAMES.get(program, program)}")
    blocked = [k for k in params if k not in allowed_in(ac, program)]
    if blocked:
        names = ", ".join(LABELS.get(k, k) for k in blocked)
        raise ValueError(f"{names} não está disponível no modo {MODE_NAMES.get(program, program)}")


def _spec(spec: dict[str, Any]) -> dict[str, Any]:
    if spec.get("typology") == "range":
        return {"tipo": "range", "min": _num(spec["minimumValue"]), "max": _num(spec["maximumValue"]), "passo": _num(spec["incrementValue"])}
    return {"tipo": "enum", "valores": [str(v) for v in spec.get("enumValues", [])]}


def device_json(ac: AirConditioner, aliases: dict[str, str]) -> dict[str, Any]:
    state = ac.state
    # Só o que o programa E o settings aceitam: em multi-split o catálogo libera
    # a autolimpeza no programa, mas bloqueia no settings.
    programs = {}
    for program in ac.programs:
        if program in MODE_PROGRAMS:
            allowed = allowed_in(ac, program)
            programs[program] = {k: _spec(v) for k, v in ac.adjustable(program).items() if k in allowed}
    return {
        "id": ac.mac,
        "nome": display_name(ac, aliases),
        "modelo": ac.appliance.get("modelName"),
        "online": ac.online,
        "ligado": ac.is_on,
        "programa": current_program(state),
        "estado": {k: state.get(k) for k in STATE_KEYS} | {k: v for k, v in ac.preferences.items() if k in REMEMBERED},
        "programas": programs,
    }


class Comando(BaseModel):
    programa: str | None = None
    parametros: dict[str, Any] = {}


def run_schedule_event(service: Service, schedule: Schedule, kind: str) -> dict[str, str]:
    """Executa um evento de agendamento em cada aparelho. Usado pela thread e pelo "testar agora"."""
    devices = {d.mac: d for d in service.devices()}
    results = {}
    for mac in schedule.aparelhos:
        ac = devices.get(mac)
        if ac is None:
            results[mac] = "erro: aparelho não encontrado"
            continue
        if not ac.online:
            results[mac] = "erro: offline"
            continue
        try:
            if kind == "ligar":
                check_allowed(ac, schedule.programa, schedule.parametros)
                payload = ac.start(schedule.programa, schedule.parametros)
            else:
                payload = ac.turn_off()
            service.send(ac, payload)
            if kind == "ligar":
                for key in REMEMBERED & set(schedule.parametros):
                    service.remember(ac, key, schedule.parametros[key])
            results[mac] = "ok"
        except (ValueError, HonError, ServiceError) as e:
            results[mac] = f"erro: {e}"
    return results


def schedule_json(s: Schedule, now: datetime) -> dict[str, Any]:
    upcoming = next_event(s, now)
    return asdict(s) | {
        "descricao": describe_when(s),
        "proximo": {"acao": upcoming[0], "quando": upcoming[1].isoformat(timespec="minutes")} if upcoming else None,
    }


class AgendamentoIn(BaseModel):
    aparelhos: list[str]
    inicio: str
    programa: str | None = "IOT_COOL"
    parametros: dict[str, Any] = {}
    frequencia: str = "diario"
    dias: list[int] = []
    data: str | None = None
    fim: str | None = None
    nome: str = ""
    ativo: bool = True


def build_router(get_service, lock: threading.Lock, schedules: ScheduleStore | None = None) -> APIRouter:
    router = APIRouter(prefix="/api")

    def find(service: Service, device_id: str) -> AirConditioner:
        ac = service.device(device_id)
        if ac is None:
            raise HTTPException(404, "Aparelho não encontrado")
        if not ac.online:
            raise HTTPException(409, f"{display_name(ac, service.aliases)} está offline")
        return ac

    def act(device_id: str, build, remember: dict[str, Any] | None = None) -> dict[str, Any]:
        remember = remember or {}
        with lock:
            service: Service = get_service()
            ac = find(service, device_id)
            try:
                payload = build(ac)
                service.send(ac, payload)
                for key in REMEMBERED & set(remember):
                    service.remember(ac, key, remember[key])
            except ValueError as e:
                raise HTTPException(400, str(e)) from None
            except (HonError, ServiceError) as e:
                raise HTTPException(502, str(e)) from None
            return device_json(ac, service.aliases)

    def editable(params: dict[str, Any]) -> dict[str, Any]:
        unknown = set(params) - EDITABLE
        if unknown:
            raise HTTPException(400, f"Parâmetros não suportados: {', '.join(sorted(unknown))}")
        return params

    @router.get("/aparelhos")
    def aparelhos() -> list[dict[str, Any]]:
        with lock:
            service: Service = get_service()
            try:
                return [device_json(ac, service.aliases) for ac in service.devices()]
            except (HonError, ServiceError) as e:
                raise HTTPException(502, str(e)) from None

    @router.post("/aparelhos/{device_id}/ligar")
    def ligar(device_id: str, cmd: Comando) -> dict[str, Any]:
        params = editable(cmd.parametros)

        def build(ac: AirConditioner) -> dict[str, Any]:
            program = cmd.programa or current_program(ac.state) or "IOT_COOL"
            if program not in MODE_PROGRAMS:
                raise ValueError(f"Programa desconhecido: {program}")
            check_allowed(ac, program, params)
            return ac.start(program, params)

        return act(device_id, build, params)

    @router.post("/aparelhos/{device_id}/ajustar")
    def ajustar(device_id: str, cmd: Comando) -> dict[str, Any]:
        params = editable(cmd.parametros)

        def build(ac: AirConditioner) -> dict[str, Any]:
            if not ac.is_on:
                raise ValueError("Aparelho desligado: use ligar")
            # Trocar de modo reinicia o programa (como o "Start now" do app).
            if cmd.programa and cmd.programa != current_program(ac.state):
                if cmd.programa not in MODE_PROGRAMS:
                    raise ValueError(f"Programa desconhecido: {cmd.programa}")
                check_allowed(ac, cmd.programa, params)
                return ac.start(cmd.programa, params)
            # settings aceita quase tudo em qualquer modo; validamos pelo modo atual.
            check_allowed(ac, current_program(ac.state) or "IOT_COOL", params)
            return ac.apply(params)

        return act(device_id, build, params)

    @router.post("/aparelhos/{device_id}/desligar")
    def desligar(device_id: str) -> dict[str, Any]:
        return act(device_id, lambda ac: ac.turn_off())

    # --- agendamentos ---------------------------------------------------------

    store = schedules or ScheduleStore()

    def validate(schedule: Schedule) -> None:
        try:
            schedule.validate()
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        if schedule.programa is None:
            return
        if schedule.programa not in MODE_PROGRAMS:
            raise HTTPException(400, f"Programa desconhecido: {schedule.programa}")
        editable(schedule.parametros)
        with lock:
            devices = {d.mac: d for d in get_service().devices()}
        for mac in schedule.aparelhos:
            if mac not in devices:
                raise HTTPException(400, f"Aparelho não encontrado: {mac}")
            try:
                check_allowed(devices[mac], schedule.programa, schedule.parametros)
            except ValueError as e:
                raise HTTPException(400, f"{display_name(devices[mac], {})}: {e}") from None

    def find_schedule(schedule_id: str) -> Schedule:
        found = store.get(schedule_id)
        if found is None:
            raise HTTPException(404, "Agendamento não encontrado")
        return found

    @router.get("/agendamentos")
    def agendamentos() -> dict[str, Any]:
        now = datetime.now()
        return {"agendamentos": [schedule_json(s, now) for s in store.all()], "historico": store.history()[:20]}

    @router.post("/agendamentos")
    def criar_agendamento(dados: AgendamentoIn) -> dict[str, Any]:
        schedule = Schedule(**dados.model_dump())
        validate(schedule)
        return schedule_json(store.save(schedule), datetime.now())

    @router.put("/agendamentos/{schedule_id}")
    def editar_agendamento(schedule_id: str, dados: AgendamentoIn) -> dict[str, Any]:
        find_schedule(schedule_id)
        schedule = Schedule(**dados.model_dump(), id=schedule_id)
        validate(schedule)
        return schedule_json(store.save(schedule), datetime.now())

    @router.delete("/agendamentos/historico")
    def apagar_historico() -> dict[str, str]:
        store.clear_history()
        return {"ok": "sim"}

    @router.delete("/agendamentos/{schedule_id}")
    def apagar_agendamento(schedule_id: str) -> dict[str, str]:
        if not store.delete(schedule_id):
            raise HTTPException(404, "Agendamento não encontrado")
        return {"ok": "sim"}

    @router.post("/agendamentos/{schedule_id}/testar")
    def testar_agendamento(schedule_id: str, acao: str = "ligar") -> dict[str, Any]:
        """Executa agora, para conferir sem esperar o horário."""
        schedule = find_schedule(schedule_id)
        if acao not in ("ligar", "desligar") or (acao == "ligar" and not schedule.programa):
            raise HTTPException(400, "Ação inválida")
        with lock:
            results = run_schedule_event(get_service(), schedule, acao)
        return {"resultado": results}

    return router
