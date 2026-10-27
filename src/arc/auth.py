"""Usuários do webapp, sessões e chaves de API (Atalhos/Siri).

Tudo fica em ~/.config/arc/auth.json (permissão 600). Senhas são guardadas com
scrypt; sessões e chaves só como SHA-256, então o arquivo vazado não dá acesso.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
from pathlib import Path
from typing import Any

AUTH_PATH = Path.home() / ".config" / "arc" / "auth.json"
MIN_PASSWORD = 8
SESSION_SECONDS = 60 * 60 * 24 * 180

_SCRYPT = {"n": 2**14, "r": 8, "p": 1}
_lock = threading.Lock()


class AuthError(Exception):
    pass


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **_SCRYPT)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, digest = stored.split("$")
    except ValueError:
        return False
    candidate = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), dklen=32, **_SCRYPT)
    return hmac.compare_digest(candidate.hex(), digest)


def _digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


# Hash de uma senha qualquer, para gastar o mesmo tempo quando o usuário não existe.
_DUMMY = hash_password(secrets.token_hex(8))


class AuthStore:
    def __init__(self, path: Path = AUTH_PATH) -> None:
        self.path = path

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text())
        except (FileNotFoundError, ValueError):
            data = {}
        for key in ("users", "sessions", "tokens"):
            data.setdefault(key, {})
        return data

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        tmp.chmod(0o600)
        tmp.replace(self.path)

    # --- usuários -------------------------------------------------------------

    def has_users(self) -> bool:
        return bool(self._load()["users"])

    def set_password(self, user: str, password: str) -> None:
        if len(password) < MIN_PASSWORD:
            raise AuthError(f"A senha precisa ter pelo menos {MIN_PASSWORD} caracteres")
        with _lock:
            data = self._load()
            data["users"][user] = {"senha": hash_password(password), "criado": int(time.time())}
            # Trocar a senha derruba as sessões abertas desse usuário.
            data["sessions"] = {k: v for k, v in data["sessions"].items() if v["usuario"] != user}
            self._save(data)

    def remove_user(self, user: str) -> None:
        with _lock:
            data = self._load()
            if data["users"].pop(user, None) is None:
                raise AuthError(f"Usuário {user} não existe")
            data["sessions"] = {k: v for k, v in data["sessions"].items() if v["usuario"] != user}
            self._save(data)

    def users(self) -> list[str]:
        return sorted(self._load()["users"])

    # --- sessões do webapp ------------------------------------------------------

    def login(self, user: str, password: str, device: str = "") -> str | None:
        """Devolve o id da sessão (vai para o cookie) ou None."""
        data = self._load()
        stored = data["users"].get(user, {}).get("senha")
        if not verify_password(password, stored or _DUMMY) or not stored:
            return None
        session = secrets.token_urlsafe(32)
        now = int(time.time())
        with _lock:
            data = self._load()
            data["sessions"][_digest(session)] = {
                "id": secrets.token_hex(3),
                "usuario": user,
                "aparelho": device[:120],
                "criado": now,
                "visto": now,
            }
            self._save(data)
        return session

    def session_user(self, session: str | None) -> str | None:
        if not session:
            return None
        data = self._load()
        entry = data["sessions"].get(_digest(session))
        if not entry or time.time() - entry["visto"] > SESSION_SECONDS:
            return None
        if time.time() - entry["visto"] > 3600:
            with _lock:
                data = self._load()
                if _digest(session) in data["sessions"]:
                    data["sessions"][_digest(session)]["visto"] = int(time.time())
                    self._save(data)
        return entry["usuario"]

    def logout(self, session: str) -> None:
        with _lock:
            data = self._load()
            data["sessions"].pop(_digest(session), None)
            self._save(data)

    def sessions(self) -> list[dict[str, Any]]:
        return sorted(self._load()["sessions"].values(), key=lambda s: s["visto"], reverse=True)

    def revoke_session(self, short_id: str) -> bool:
        with _lock:
            data = self._load()
            keep = {k: v for k, v in data["sessions"].items() if v["id"] != short_id}
            changed = len(keep) != len(data["sessions"])
            data["sessions"] = keep
            self._save(data)
        return changed

    # --- chaves de API (Atalhos) -------------------------------------------------

    def create_token(self, name: str) -> str:
        token = "arc_" + secrets.token_urlsafe(24)
        with _lock:
            data = self._load()
            if name in data["tokens"]:
                raise AuthError(f"Já existe uma chave chamada {name}")
            data["tokens"][name] = {"hash": _digest(token), "criado": int(time.time())}
            self._save(data)
        return token

    def token_name(self, token: str | None) -> str | None:
        if not token:
            return None
        digest = _digest(token)
        for name, entry in self._load()["tokens"].items():
            if hmac.compare_digest(entry["hash"], digest):
                return name
        return None

    def tokens(self) -> dict[str, Any]:
        return self._load()["tokens"]

    def revoke_token(self, name: str) -> bool:
        with _lock:
            data = self._load()
            found = data["tokens"].pop(name, None) is not None
            self._save(data)
        return found


class RateLimiter:
    """Limita tentativas de login por IP (janela deslizante)."""

    def __init__(self, attempts: int = 5, window: float = 300) -> None:
        self.attempts = attempts
        self.window = window
        self._hits: dict[str, list[float]] = {}

    def allow(self, key: str) -> bool:
        now = time.time()
        hits = [t for t in self._hits.get(key, []) if now - t < self.window]
        self._hits[key] = hits
        return len(hits) < self.attempts

    def fail(self, key: str) -> None:
        self._hits.setdefault(key, []).append(time.time())

    def reset(self, key: str) -> None:
        self._hits.pop(key, None)
