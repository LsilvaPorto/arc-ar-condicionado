from datetime import datetime, timedelta

import pytest

from arc.schedule import (
    Schedule,
    Scheduler,
    ScheduleStore,
    describe_when,
    due_events,
    missed_events,
    next_event,
)

# 2026-10-27 é uma terça-feira.
TUE = datetime(2026, 10, 27)


def at(day: datetime, hhmm: str, plus_min: int = 0) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return day.replace(hour=h, minute=m) + timedelta(minutes=plus_min)


def noite(**kw):
    base = dict(aparelhos=["m1"], inicio="22:30", fim="07:00", programa="IOT_COOL",
                parametros={"tempSel": 23, "silentSleepStatus": "1", "screenDisplayStatus": "0"})
    return Schedule(**{**base, **kw})


def kinds(events):
    return [(k, w.strftime("%d %H:%M")) for k, w, _ in events]


def test_intervalo_que_passa_da_meia_noite():
    s = noite()
    assert kinds(due_events(s, at(TUE, "22:31"), set())) == [("ligar", "27 22:30")]
    assert kinds(due_events(s, at(TUE + timedelta(days=1), "07:02"), set())) == [("desligar", "28 07:00")]
    assert due_events(s, at(TUE, "22:29"), set()) == []


def test_nao_repete_evento_ja_feito():
    s = noite()
    _, _, key = due_events(s, at(TUE, "22:31"), set())[0]
    assert due_events(s, at(TUE, "22:33"), {key}) == []


def test_janela_de_tolerancia():
    s = noite()
    assert due_events(s, at(TUE, "22:40"), set())  # 10 min depois ainda roda
    assert due_events(s, at(TUE, "22:41"), set()) == []  # passou da janela
    missed = missed_events(s, at(TUE, "23:30"), last_tick=at(TUE, "22:00"), done=set())
    assert kinds(missed) == [("ligar", "27 22:30")]


def test_semanal_so_nos_dias_escolhidos():
    s = noite(frequencia="semanal", dias=[0, 2], fim=None)  # seg e qua
    assert due_events(s, at(TUE, "22:31"), set()) == []
    assert due_events(s, at(TUE + timedelta(days=1), "22:31"), set())
    # o fim de um intervalo iniciado na segunda cai na terça
    s2 = noite(frequencia="semanal", dias=[0])
    assert kinds(due_events(s2, at(TUE, "07:01"), set())) == [("desligar", "27 07:00")]


def test_uma_vez_e_proximo_evento():
    s = noite(frequencia="uma_vez", data="2026-10-28", fim=None)
    assert due_events(s, at(TUE, "22:31"), set()) == []
    assert next_event(s, at(TUE, "12:00")) == ("ligar", at(TUE + timedelta(days=1), "22:30"))
    assert next_event(noite(), at(TUE, "23:00")) == ("desligar", at(TUE + timedelta(days=1), "07:00"))
    assert next_event(noite(ativo=False), at(TUE, "23:00")) is None


def test_so_desligar():
    s = Schedule(aparelhos=["m1"], inicio="01:00", programa=None)
    assert kinds(due_events(s, at(TUE, "01:00"), set())) == [("desligar", "27 01:00")]


@pytest.mark.parametrize("kw, msg", [
    ({"aparelhos": []}, "aparelho"),
    ({"inicio": "25:00"}, "Horário"),
    ({"fim": "22:30"}, "iguais"),
    ({"frequencia": "semanal", "dias": []}, "dia"),
    ({"frequencia": "uma_vez", "data": None}, "data"),
    ({"programa": None}, "fim"),
])
def test_validacao(kw, msg):
    with pytest.raises(ValueError, match=msg):
        noite(**kw).validate()


def test_descricao():
    assert describe_when(noite()) == "Todo dia · 22:30–07:00"
    assert describe_when(noite(frequencia="semanal", dias=[0, 1, 2, 3, 4], fim=None)) == "Seg a sex · 22:30"
    assert describe_when(noite(frequencia="uma_vez", data="2026-12-24")) == "24/12 · 22:30–07:00"


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


def test_scheduler_executa_registra_e_tenta_de_novo_se_offline(tmp_path):
    store = ScheduleStore(tmp_path / "agenda.json")
    s = store.save(noite())
    calls = []
    answers = iter([{"m1": "erro: offline"}, {"m1": "ok"}])

    def run(schedule, kind):
        calls.append(kind)
        return next(answers)

    clock = Clock(at(TUE, "22:00"))
    sched = Scheduler(store, run, clock)
    clock.now = at(TUE, "22:30")
    sched.tick()  # offline: não registra, tenta de novo
    assert calls == ["ligar"] and store.history() == []
    clock.now = at(TUE, "22:31")
    sched.tick()
    assert calls == ["ligar", "ligar"]
    assert store.history()[0]["resultado"] == {"m1": "ok"}
    sched.tick()  # já feito
    assert calls == ["ligar", "ligar"]
    assert store.get(s.id).ativo


def test_scheduler_marca_perdido_e_desativa_uma_vez(tmp_path):
    store = ScheduleStore(tmp_path / "agenda.json")
    s = store.save(noite(frequencia="uma_vez", data="2026-10-27", fim=None))
    clock = Clock(at(TUE, "22:00"))
    sched = Scheduler(store, lambda *_: {"m1": "ok"}, clock)
    clock.now = at(TUE, "23:30")  # Mac dormiu durante o horário
    sched.tick()
    assert "perdido" in store.history()[0]["resultado"]["*"]
    assert store.get(s.id).ativo is False
