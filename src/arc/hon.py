"""Cliente da nuvem hOn (Haier), usando o login CIAM introduzido em 2026-06.

Referência do protocolo: https://github.com/gvigroux/hon (custom_components/hon/hon.py).
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

API_URL = "https://api-iot.he.services"
APP_VERSION = "2.27.9"
OS = "android"
OS_VERSION = 31
DEVICE_MODEL = "exynos9820"

# Os tokens CIAM expiram em ~15 min; renovamos antes disso.
SESSION_TIMEOUT = 600

CLIMATE_TYPE_ID = 11


class HonError(Exception):
    pass


class HonAuthError(HonError):
    pass


@dataclass
class Tokens:
    cognito_token: str
    id_token: str
    refresh_token: str = ""
    created_at: float = field(default_factory=time.time)

    @property
    def expired(self) -> bool:
        return time.time() - self.created_at > SESSION_TIMEOUT


def _pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


class HonClient:
    def __init__(self, email: str, password: str, http: httpx.Client | None = None) -> None:
        self._email = email
        self._password = password
        self._http = http or httpx.Client(
            base_url=API_URL,
            timeout=20,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/102.0.0.0 Safari/537.36"},
        )
        self._tokens: Tokens | None = None

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> HonClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- autenticação ---------------------------------------------------

    def authorize(self) -> Tokens:
        verifier, challenge = _pkce_pair()

        resp = self._http.get(
            "/ciam/authorize",
            params={"username": self._email, "password": self._password, "code_challenge": challenge},
        )
        if resp.status_code != 200:
            raise HonAuthError(f"/ciam/authorize respondeu HTTP {resp.status_code}: {resp.text[:300]}")
        session_id = resp.json().get("session_id")
        if not session_id:
            raise HonAuthError("Login recusado (sem session_id). Confira e-mail e senha.")

        resp = self._http.post("/ciam/token", json={"session_id": session_id, "code_verifier": verifier})
        try:
            tokens = resp.json()["tokens"]
            self._tokens = Tokens(tokens["cognito_token"], tokens["id_token"], tokens.get("refresh_token", ""))
        except (KeyError, TypeError, ValueError):
            raise HonAuthError(f"/ciam/token sem tokens (HTTP {resp.status_code}): {resp.text[:300]}") from None
        return self._tokens

    def _headers(self) -> dict[str, str]:
        if self._tokens is None or self._tokens.expired:
            self.authorize()
        assert self._tokens
        return {
            "Content-Type": "application/json",
            "cognito-token": self._tokens.cognito_token,
            "id-token": self._tokens.id_token,
        }

    # --- leitura ----------------------------------------------------------

    def appliance_list_raw(self) -> dict[str, Any]:
        resp = self._http.post(
            "/unified-api/v1/view/appliance-list", headers=self._headers(), json={"deviceId": "arc-cli"}
        )
        resp.raise_for_status()
        return resp.json()

    def appliances(self) -> list[dict[str, Any]]:
        data = self.appliance_list_raw()
        try:
            items = data["modules"]["applianceList"]["payload"]["appliances"]
        except (KeyError, TypeError):
            raise HonError(f"Resposta inesperada do appliance-list: {str(data)[:500]}") from None
        return [a for a in items if "macAddress" in a and "applianceTypeId" in a]

    def legacy_appliances_raw(self) -> Any:
        """Endpoint antigo; segundo o fork, volta vazio desde 2026-06. Só para diagnóstico."""
        resp = self._http.get("/commands/v1/appliance", headers=self._headers())
        return {"status": resp.status_code, "body": _safe_json(resp)}

    def commands(self, appliance: dict[str, Any]) -> dict[str, Any]:
        params = {
            "applianceType": appliance["applianceTypeName"],
            "code": appliance.get("code", ""),
            "applianceModelId": appliance.get("applianceModelId", ""),
            "firmwareId": appliance.get("eepromId", ""),
            "macAddress": appliance["macAddress"],
            "fwVersion": appliance.get("fwVersion", ""),
            "os": OS,
            "appVersion": APP_VERSION,
            "series": appliance.get("series", ""),
        }
        resp = self._http.get("/commands/v1/retrieve", params=params, headers=self._headers())
        resp.raise_for_status()
        return resp.json().get("payload", {})

    def context(self, appliance: dict[str, Any]) -> dict[str, Any]:
        params = {
            "macAddress": appliance["macAddress"],
            "applianceType": appliance["applianceTypeName"],
            "category": "CYCLE",
        }
        resp = self._http.get("/commands/v1/context", params=params, headers=self._headers())
        resp.raise_for_status()
        return resp.json().get("payload", {})


    # --- escrita ----------------------------------------------------------

    def send(self, payload: dict[str, Any]) -> None:
        resp = self._http.post("/commands/v1/send", headers=self._headers(), json=payload)
        data = _safe_json(resp)
        result = data.get("payload", {}).get("resultCode") if isinstance(data, dict) else None
        if result != "0":
            raise HonError(f"Comando recusado (HTTP {resp.status_code}): {str(data)[:300]}")


def _safe_json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return resp.text[:500]


def shadow_parameters(context: dict[str, Any]) -> dict[str, str]:
    """Estado atual do aparelho (parNewVal de cada parâmetro do shadow)."""
    params = (context.get("shadow") or {}).get("parameters") or {}
    return {name: v.get("parNewVal") for name, v in params.items()}
