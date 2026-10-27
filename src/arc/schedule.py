"""Agendamentos: quando ligar/desligar cada aparelho.

O cálculo de horários é puro (recebe `now`) para poder ser testado; quem
executa é o `Scheduler`, uma thread dentro do servidor.
"""

from __future__ import annotations

import json
import logging
import secrets
import threading
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("arc.agenda")

SCHEDULE_PATH = Path.home() / ".config" / "arc" / "agendamentos.json"
# Se o Mac estava dormindo no horário, ainda executa se acordar dentro dessa janela.
GRACE = timedelta(minutes=10)
TICK_SECONDS = 20
HISTORY_SIZE = 60

FREQUENCIES = ("uma_vez", "diario", "semanal")
WEEKDAYS = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]


@dataclass
class Schedule:
    aparelhos: list[str]
    inicio: str  # "HH:MM"
    programa: str | None = "IOT_COOL"  # None = só desliga no início
    parametros: dict[str, Any] = field(default_factory=dict)
    frequencia: str = "diario"
    dias: list[int] = field(default_factory=list)  # 0 = segunda
    data: str | None = None  # "YYYY-MM-DD" para uma_vez
    fim: str | None = None  # "HH:MM": desliga nesse horário
    nome: str = ""
    ativo: bool = True
    id: str = field(default_factory=lambda: secrets.token_hex(4))

    def validate(self) -> None:
        if not self.aparelhos:
            raise ValueError("Escolha pelo menos um aparelho")
        _parse_time(self.inicio)
        if self.fim is not None:
            _parse_time(self.fim)
            if self.programa is None:
                raise ValueError("Horário de fim só faz sentido quando o agendamento liga")
            if self.fim == self.inicio:
                raise ValueError("Início e fim não podem ser iguais")
        if self.frequencia not in FREQUENCIES:
            raise ValueError(f"Frequência inválida: {self.frequencia}")
        if self.frequencia == "semanal" and not self.dias:
            raise ValueError("Escolha pelo menos um dia da semana")
        if any(d not in range(7) for d in self.dias):
            raise ValueError("Dia da semana inválido")
        if self.frequencia == "uma_vez":
            if not self.data:
                raise ValueError("Escolha a data")
            date.fromisoformat(self.data)


def _parse_time(value: str) -> tuple[int, int]:
    try:
        hour, minute = (int(x) for x in value.split(":"))
    except (ValueError, AttributeError):
        raise ValueError(f"Horário inválido: {value}") from None
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ValueError(f"Horário inválido: {value}")
    return hour, minute


def _at(day: date, hhmm: str) -> datetime:
    hour, minute = _parse_time(hhmm)
    return datetime(day.year, day.month, day.day, hour, minute)


def runs_on(s: Schedule, day: date) -> bool:
    """O agendamento começa nesse dia?"""
    if s.frequencia == "diario":
        return True
    if s.frequencia == "semanal":
        return day.weekday() in s.dias
    return s.data == day.isoformat()


def events_for_start_day(s: Schedule, day: date) -> list[tuple[str, datetime]]:
    """Eventos ("ligar"/"desligar") de uma ocorrência que começa em `day`."""
    start = _at(day, s.inicio)
    events = [("ligar" if s.programa else "desligar", start)]
    if s.fim:
        end = _at(day, s.fim)
        if end <= start:  # passa da meia-noite
            end += timedelta(days=1)
        events.append(("desligar", end))
    return events


def due_events(s: Schedule, now: datetime, done: set[str]) -> list[tuple[str, datetime, str]]:
    """Eventos que já deviam ter rodado (dentro da janela) e ainda não rodaram."""
    if not s.ativo:
        return []
    result = []
    for back in (0, 1, 2):
        day = now.date() - timedelta(days=back)
        if not runs_on(s, day):
            continue
        for kind, when in events_for_start_day(s, day):
            key = f"{s.id}:{kind}:{when.isoformat(timespec='minutes')}"
            if when <= now <= when + GRACE and key not in done:
                result.append((kind, when, key))
    return sorted(result, key=lambda e: e[1])


def missed_events(s: Schedule, now: datetime, last_tick: datetime, done: set[str]) -> list[tuple[str, datetime, str]]:
    """Eventos cuja janela passou entre o último tick e agora sem executar (Mac dormindo)."""
    if not s.ativo:
        return []
    result = []
    for back in range(0, 9):
        day = now.date() - timedelta(days=back)
        if not runs_on(s, day):
            continue
        for kind, when in events_for_start_day(s, day):
            key = f"{s.id}:{kind}:{when.isoformat(timespec='minutes')}"
            if last_tick < when and when + GRACE < now and key not in done:
                result.append((kind, when, key))
    return result


def next_event(s: Schedule, now: datetime) -> tuple[str, datetime] | None:
    if not s.ativo:
        return None
    upcoming = []
    for ahead in range(-1, 9):
        day = now.date() + timedelta(days=ahead)
        if runs_on(s, day):
            upcoming += [(k, w) for k, w in events_for_start_day(s, day) if w > now]
    return min(upcoming, key=lambda e: e[1]) if upcoming else None


def finished(s: Schedule, now: datetime) -> bool:
    """Agendamento de uma vez que já passou por inteiro."""
    if s.frequencia != "uma_vez" or not s.data:
        return False
    last = max(w for _, w in events_for_start_day(s, date.fromisoformat(s.data)))
    return now > last + GRACE


# --- armazenamento ---------------------------------------------------------------


class ScheduleStore:
    def __init__(self, path: Path = SCHEDULE_PATH) -> None:
        self.path = path
        self._lock = threading.RLock()

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text())
        except (FileNotFoundError, ValueError):
            data = {}
        data.setdefault("agendamentos", [])
        data.setdefault("feitos", [])
        data.setdefault("historico", [])
        return data

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        tmp.replace(self.path)

    def all(self) -> list[Schedule]:
        return [Schedule(**s) for s in self._load()["agendamentos"]]

    def get(self, schedule_id: str) -> Schedule | None:
        return next((s for s in self.all() if s.id == schedule_id), None)

    def save(self, schedule: Schedule) -> Schedule:
        schedule.validate()
        with self._lock:
            data = self._load()
            items = [s for s in data["agendamentos"] if s["id"] != schedule.id]
            items.append(asdict(schedule))
            data["agendamentos"] = sorted(items, key=lambda s: s["inicio"])
            self._save(data)
        return schedule

    def delete(self, schedule_id: str) -> bool:
        with self._lock:
            data = self._load()
            before = len(data["agendamentos"])
            data["agendamentos"] = [s for s in data["agendamentos"] if s["id"] != schedule_id]
            self._save(data)
            return len(data["agendamentos"]) != before

    def done_keys(self) -> set[str]:
        return set(self._load()["feitos"])

    def record(self, key: str | None, entry: dict[str, Any]) -> None:
        with self._lock:
            data = self._load()
            if key:
                # Guarda só chaves recentes: o suficiente para a janela de execução.
                data["feitos"] = (data["feitos"] + [key])[-500:]
            data["historico"] = ([entry] + data["historico"])[:HISTORY_SIZE]
            self._save(data)

    def history(self) -> list[dict[str, Any]]:
        return self._load()["historico"]

    def clear_history(self) -> None:
        # "feitos" fica: é o que impede um evento já executado de rodar de novo.
        with self._lock:
            data = self._load()
            data["historico"] = []
            self._save(data)


# --- execução --------------------------------------------------------------------


class Scheduler:
    """Thread que confere os agendamentos a cada TICK_SECONDS.

    `run_event(schedule, kind)` executa de fato e devolve {mac: "ok" | "erro: ..."}.
    """

    def __init__(self, store: ScheduleStore, run_event: Callable[[Schedule, str], dict[str, str]],
                 clock: Callable[[], datetime] = datetime.now) -> None:
        self.store = store
        self.run_event = run_event
        self.clock = clock
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_tick = clock()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="arc-agenda", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(TICK_SECONDS):
            try:
                self.tick()
            except Exception:  # a thread nunca pode morrer
                log.exception("erro no agendador")

    def tick(self) -> None:
        now = self.clock()
        done = self.store.done_keys()
        for s in self.store.all():
            for kind, when, key in missed_events(s, now, self._last_tick, done):
                self.store.record(key, _entry(s, kind, when, {"*": "perdido (servidor parado ou Mac dormindo)"}))
            for kind, when, key in due_events(s, now, done):
                results = self.run_event(s, kind)
                failed = {mac: r for mac, r in results.items() if r != "ok"}
                offline_only = failed and all("offline" in r for r in failed.values())
                if offline_only and now < when + GRACE - timedelta(seconds=TICK_SECONDS):
                    # Tenta de novo no próximo tick enquanto estiver dentro da janela.
                    log.info("agendamento %s: aparelho offline, tentando de novo", s.id)
                    continue
                self.store.record(key, _entry(s, kind, when, results))
            if finished(s, now) and s.ativo:
                s.ativo = False
                self.store.save(s)
        self._last_tick = now


def _entry(s: Schedule, kind: str, when: datetime, results: dict[str, str]) -> dict[str, Any]:
    return {
        "agendamento": s.id,
        "nome": s.nome,
        "acao": kind,
        "previsto": when.isoformat(timespec="minutes"),
        "executado": datetime.now().isoformat(timespec="seconds"),
        "resultado": results,
    }


def describe_when(s: Schedule) -> str:
    if s.frequencia == "diario":
        days = "Todo dia"
    elif s.frequencia == "semanal":
        days = "Seg a sex" if sorted(s.dias) == [0, 1, 2, 3, 4] else (
            "Fim de semana" if sorted(s.dias) == [5, 6] else ", ".join(WEEKDAYS[d] for d in sorted(s.dias)))
    else:
        days = date.fromisoformat(s.data).strftime("%d/%m") if s.data else "?"
    hours = f"{s.inicio}–{s.fim}" if s.fim else s.inicio
    return f"{days} · {hours}"

