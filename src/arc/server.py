"""Servidor HTTP local para os Atalhos do iOS / Siri.

As respostas são texto curto em português, pronto para a Siri falar.
"""

from __future__ import annotations

import threading
from contextlib import asynccontextmanager

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from arc.auth import AuthStore, RateLimiter
from arc.hon import HonError
from arc.names import normalize
from arc.service import Service, ServiceError
from arc.schedule import Scheduler, ScheduleStore
from arc.web import STATIC_DIR, build_router, run_schedule_event

schedules = ScheduleStore()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    def run(schedule, kind):
        with _lock:
            return run_schedule_event(_get_service(), schedule, kind)

    scheduler = Scheduler(schedules, run)
    scheduler.start()
    yield
    scheduler.stop()


app = FastAPI(title="arc", docs_url=None, redoc_url=None, lifespan=lifespan)

COOKIE = "arc_sessao"
store = AuthStore()
limiter = RateLimiter()

_lock = threading.Lock()
_service: Service | None = None


def _get_service() -> Service:
    global _service
    if _service is None:
        _service = Service()
    return _service


def _auth(request: Request, authorization: str = Header(""), arc_sessao: str | None = Cookie(None)) -> str:
    """Sessão do webapp (cookie) ou chave de API dos Atalhos (Bearer)."""
    user = store.session_user(arc_sessao)
    if user:
        # Cookie só vale vindo do próprio webapp (bloqueia POST de outros sites).
        origin = request.headers.get("origin")
        if request.method != "GET" and origin and origin.split("://")[-1] != request.headers.get("host"):
            raise HTTPException(403, "origem não permitida")
        return user
    token = store.token_name(authorization.removeprefix("Bearer ").strip())
    if token:
        return f"chave:{token}"
    raise HTTPException(401, "não autenticado")


class Pedido(BaseModel):
    aparelho: str = "tudo"
    temperatura: float | None = None
    modo: str | None = None
    ventilador: str | None = None


def _clean(value: str | None) -> str | None:
    # Atalhos mandam campo vazio quando o usuário não escolhe nada.
    return normalize(value) or None if value else None


def _speak(lines: list[str]) -> PlainTextResponse:
    return PlainTextResponse(". ".join(lines) + ".")


def _run(action) -> PlainTextResponse:
    with _lock:
        try:
            return _speak(action(_get_service()))
        except (ServiceError, HonError) as e:
            return PlainTextResponse(str(e), status_code=400)


@app.get("/saude")
def saude() -> dict[str, str]:
    return {"ok": "sim"}


@app.get("/status", dependencies=[Depends(_auth)])
def status(aparelho: str | None = None) -> PlainTextResponse:
    return _run(lambda s: s.status(_clean(aparelho)))


@app.post("/ligar", dependencies=[Depends(_auth)])
def ligar(p: Pedido) -> PlainTextResponse:
    return _run(lambda s: s.turn_on(p.aparelho, _clean(p.modo), p.temperatura, _clean(p.ventilador)))


@app.post("/desligar", dependencies=[Depends(_auth)])
def desligar(p: Pedido) -> PlainTextResponse:
    return _run(lambda s: s.turn_off(p.aparelho))


@app.post("/ajustar", dependencies=[Depends(_auth)])
def ajustar(p: Pedido) -> PlainTextResponse:
    return _run(lambda s: s.adjust(p.aparelho, _clean(p.modo), p.temperatura, _clean(p.ventilador)))


# --- webapp -----------------------------------------------------------------

app.include_router(build_router(_get_service, _lock, schedules), dependencies=[Depends(_auth)])
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class Login(BaseModel):
    usuario: str
    senha: str


@app.get("/entrar")
def entrar_pagina(arc_sessao: str | None = Cookie(None)):
    if store.session_user(arc_sessao):
        return RedirectResponse("/", status_code=303)
    return FileResponse(STATIC_DIR / "entrar.html")


@app.post("/api/entrar")
def entrar(dados: Login, request: Request, response: Response) -> dict[str, str]:
    ip = request.client.host if request.client else "?"
    if not limiter.allow(ip):
        raise HTTPException(429, "Muitas tentativas. Espere alguns minutos.")
    session = store.login(dados.usuario.strip().lower(), dados.senha, request.headers.get("user-agent", ""))
    if not session:
        limiter.fail(ip)
        raise HTTPException(401, "Usuário ou senha incorretos")
    limiter.reset(ip)
    response.set_cookie(COOKIE, session, max_age=60 * 60 * 24 * 180, httponly=True, samesite="strict")
    return {"ok": "sim"}


@app.post("/api/sair")
def sair(response: Response, arc_sessao: str | None = Cookie(None)) -> dict[str, str]:
    if arc_sessao:
        store.logout(arc_sessao)
    response.delete_cookie(COOKIE)
    return {"ok": "sim"}


@app.get("/")
def index(arc_sessao: str | None = Cookie(None)):
    if not store.session_user(arc_sessao):
        return RedirectResponse("/entrar", status_code=303)
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/manifest.webmanifest")
def manifest() -> FileResponse:
    return FileResponse(STATIC_DIR / "manifest.webmanifest", media_type="application/manifest+json")
