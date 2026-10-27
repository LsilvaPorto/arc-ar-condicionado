import pytest
from fastapi.testclient import TestClient

from arc import server
from arc.auth import AuthError, AuthStore, RateLimiter


class FakeService:
    def __init__(self):
        self.calls = []

    def status(self, query):
        return ["Ar Quarto: desligado, ambiente 18,5°"]

    def turn_on(self, *args):
        self.calls.append(("on", *args))
        return ["Ar Quarto: ligado no frio em 22°"]


@pytest.fixture
def ctx(monkeypatch, tmp_path):
    store = AuthStore(tmp_path / "auth.json")
    store.set_password("lucas", "senha-forte")
    fake = FakeService()
    monkeypatch.setattr(server, "store", store)
    monkeypatch.setattr(server, "limiter", RateLimiter(attempts=3))
    monkeypatch.setattr(server, "_service", fake)
    return TestClient(server.app), store, fake


def test_sem_login_redireciona_e_recusa(ctx):
    c, _, _ = ctx
    assert c.get("/", follow_redirects=False).headers["location"] == "/entrar"
    assert c.get("/status").status_code == 401
    assert c.get("/api/aparelhos").status_code == 401


def test_login_cria_sessao_e_sair_encerra(ctx):
    c, store, _ = ctx
    assert c.post("/api/entrar", json={"usuario": "lucas", "senha": "errada"}).status_code == 401
    r = c.post("/api/entrar", json={"usuario": "Lucas ", "senha": "senha-forte"})
    assert r.status_code == 200
    assert c.get("/", follow_redirects=False).status_code == 200
    assert c.get("/status").text == "Ar Quarto: desligado, ambiente 18,5°."
    assert len(store.sessions()) == 1
    c.post("/api/sair")
    assert c.get("/status").status_code == 401
    assert store.sessions() == []


def test_limite_de_tentativas(ctx):
    c, _, _ = ctx
    for _ in range(3):
        assert c.post("/api/entrar", json={"usuario": "lucas", "senha": "x"}).status_code == 401
    assert c.post("/api/entrar", json={"usuario": "lucas", "senha": "senha-forte"}).status_code == 429


def test_post_com_cookie_de_outro_site_e_recusado(ctx):
    c, _, _ = ctx
    c.post("/api/entrar", json={"usuario": "lucas", "senha": "senha-forte"})
    r = c.post("/ligar", json={"aparelho": "quarto"}, headers={"Origin": "http://malicioso.example"})
    assert r.status_code == 403


def test_chave_de_api_para_atalhos(ctx):
    c, store, fake = ctx
    key = store.create_token("siri")
    h = {"Authorization": f"Bearer {key}"}
    r = c.post("/ligar", headers=h, json={"aparelho": "quarto", "temperatura": 22, "modo": "Frio", "ventilador": ""})
    assert r.text == "Ar Quarto: ligado no frio em 22°."
    assert fake.calls == [("on", "quarto", "frio", 22.0, None)]
    store.revoke_token("siri")
    assert c.get("/status", headers=h).status_code == 401


def test_arquivo_nao_guarda_segredos_em_claro(tmp_path):
    store = AuthStore(tmp_path / "auth.json")
    store.set_password("lucas", "senha-forte")
    session = store.login("lucas", "senha-forte")
    key = store.create_token("siri")
    raw = (tmp_path / "auth.json").read_text()
    assert "senha-forte" not in raw and session not in raw and key not in raw
    assert oct((tmp_path / "auth.json").stat().st_mode & 0o777) == "0o600"
    with pytest.raises(AuthError):
        store.set_password("curta", "123")


def test_trocar_senha_derruba_sessoes(tmp_path):
    store = AuthStore(tmp_path / "auth.json")
    store.set_password("lucas", "senha-forte")
    session = store.login("lucas", "senha-forte")
    store.set_password("lucas", "outra-senha")
    assert store.session_user(session) is None
