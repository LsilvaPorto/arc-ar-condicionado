"""Ações de alto nível usadas pela CLI e pelo servidor dos Atalhos."""

from __future__ import annotations

import json
import time
from typing import Any

from arc import credentials, names
from arc.climate import FAN, MODES, AirConditioner, load_all
from arc.hon import HonClient, HonError


PENDING_SECONDS = 20
# Para montar um comando, um estado com até alguns segundos basta (o webapp atualiza a cada 10 s).
CACHE_SECONDS = 15


class ServiceError(Exception):
    pass


def _deg(value: float) -> str:
    return f"{value:g}".replace(".", ",") + "°"


def display_name(ac: AirConditioner, aliases: dict[str, str]) -> str:
    alias = next((a for a, mac in aliases.items() if mac == ac.mac), None)
    if ac.appliance.get("nickName"):
        return ac.name
    return alias.capitalize() if alias else ac.name


def describe(ac: AirConditioner, aliases: dict[str, str]) -> str:
    s = ac.summary()
    name = display_name(ac, aliases)
    if not s["online"]:
        return f"{name}: offline (ambiente {_deg(s['ambiente'])})" if s["ambiente"] else f"{name}: offline"
    if not s["ligado"]:
        return f"{name}: desligado, ambiente {_deg(s['ambiente'])}"
    return f"{name}: ligado no {s['modo']} em {_deg(s['alvo'])}, ambiente {_deg(s['ambiente'])}, ventilador {s['ventilador']}"


class Service:
    def __init__(self, client: HonClient | None = None) -> None:
        if client is None:
            creds = credentials.load()
            if not creds:
                raise ServiceError("Sem credenciais. Rode `arc login` primeiro.")
            client = HonClient(*creds)
        self.client = client
        self.aliases = names.load_aliases()
        self._catalogs: dict[str, dict[str, Any]] = {}
        # Parâmetros enviados há pouco: a nuvem leva alguns segundos para refletir.
        self._pending: dict[str, tuple[float, dict[str, str]]] = {}
        self._cache: tuple[float, list[AirConditioner]] | None = None

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> Service:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def devices(self) -> list[AirConditioner]:
        devices = load_all(self.client, self._catalogs)
        prefs = names.load_preferences()
        for ac in devices:
            self._overlay(ac, prefs)
        self._cache = (time.time(), devices)
        return devices

    def device(self, mac: str) -> AirConditioner | None:
        """Um aparelho para receber comando: usa o estado recente em memória
        e, se estiver velho, consulta só esse aparelho (não os três)."""
        if self._cache is None:
            self.devices()
        assert self._cache is not None
        loaded_at, devices = self._cache
        ac = next((d for d in devices if d.mac == mac), None)
        if ac is not None and time.time() - loaded_at > CACHE_SECONDS:
            ac.context = self.client.context(ac.appliance)
            self._overlay(ac, names.load_preferences())
        return ac

    def _overlay(self, ac: AirConditioner, prefs: dict[str, dict[str, str]]) -> None:
        ac.preferences = prefs.get(ac.mac, {})
        sent_at, params = self._pending.get(ac.mac, (0, {}))
        if time.time() - sent_at > PENDING_SECONDS:
            self._pending.pop(ac.mac, None)
            return
        shadow = ac.context.setdefault("shadow", {}).setdefault("parameters", {})
        for key, value in params.items():
            shadow.setdefault(key, {})["parNewVal"] = value

    def remember(self, ac: AirConditioner, key: str, value: str) -> None:
        names.save_preference(ac.mac, key, value)
        ac.preferences = {**ac.preferences, key: str(value)}

    def send(self, ac: AirConditioner, payload: dict[str, Any]) -> None:
        self.client.send(payload)
        self._pending[ac.mac] = (time.time(), payload["parameters"])
        shadow = ac.context.setdefault("shadow", {}).setdefault("parameters", {})
        for key, value in payload["parameters"].items():
            shadow.setdefault(key, {})["parNewVal"] = value

    def find(self, query: str) -> list[AirConditioner]:
        try:
            return names.resolve(query, self.devices(), self.aliases)
        except LookupError as e:
            raise ServiceError(str(e)) from None

    def status(self, query: str | None = None) -> list[str]:
        devices = self.find(query) if query else self.devices()
        return [describe(d, self.aliases) for d in devices]

    def _run(self, targets: list[AirConditioner], build, done: str, dry_run: bool, force: bool) -> list[str]:
        lines = []
        for ac in targets:
            name = display_name(ac, self.aliases)
            if not ac.online and not force and not dry_run:
                lines.append(f"{name}: offline, comando não enviado")
                continue
            try:
                payload = build(ac)
            except (ValueError, HonError) as e:
                lines.append(f"{name}: {e}")
                continue
            if dry_run:
                lines.append(f"{name} (simulação):\n" + json.dumps(payload, indent=2, ensure_ascii=False))
                continue
            try:
                self.send(ac, payload)
                lines.append(f"{name}: {done}")
            except HonError as e:
                lines.append(f"{name}: falhou, {e}")
        return lines

    def turn_on(self, query: str, mode: str | None = None, temp: float | None = None, fan: str | None = None,
                dry_run: bool = False, force: bool = False) -> list[str]:
        _check(mode, fan)
        parts = ["ligado"] + ([f"no {mode}"] if mode else []) + ([f"em {_deg(temp)}"] if temp is not None else [])
        return self._run(self.find(query), lambda ac: ac.turn_on(mode, temp, fan), " ".join(parts), dry_run, force)

    def turn_off(self, query: str, dry_run: bool = False, force: bool = False) -> list[str]:
        return self._run(self.find(query), lambda ac: ac.turn_off(), "desligado", dry_run, force)

    def adjust(self, query: str, mode: str | None = None, temp: float | None = None, fan: str | None = None,
               dry_run: bool = False, force: bool = False) -> list[str]:
        _check(mode, fan)
        if mode is None and temp is None and fan is None:
            raise ServiceError("Diga o que mudar: temperatura, modo ou ventilador.")

        def build(ac: AirConditioner) -> dict[str, Any]:
            # settings força onOffStatus=1, então só usamos com o aparelho já ligado.
            if not ac.is_on:
                raise ValueError("está desligado (use `arc on` para ligar já com a configuração)")
            return ac.adjust(mode, temp, fan)

        changes = [c for c in [mode and f"modo {mode}", temp is not None and _deg(temp), fan and f"ventilador {fan}"] if c]
        return self._run(self.find(query), build, "ajustado: " + ", ".join(changes), dry_run, force)

    def set_alias(self, query: str, alias: str) -> str:
        targets = self.find(query)
        if len(targets) != 1:
            raise ServiceError("Escolha um aparelho só para o apelido.")
        names.save_alias(alias, targets[0].mac)
        self.aliases = names.load_aliases()
        return f"{targets[0].name} agora atende por '{names.normalize(alias)}'"


def _check(mode: str | None, fan: str | None) -> None:
    if mode is not None and mode not in MODES:
        raise ServiceError(f"Modo inválido: {mode}. Use: {', '.join(MODES)}")
    if fan is not None and fan not in FAN:
        raise ServiceError(f"Ventilador inválido: {fan}. Use: {', '.join(FAN)}")
