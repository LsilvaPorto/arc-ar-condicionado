"""Apelidos dos aparelhos (ex.: "sala" -> MAC) em ~/.config/arc/config.json."""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path
from typing import TypeVar

CONFIG_PATH = Path.home() / ".config" / "arc" / "config.json"
ALL_WORDS = {"tudo", "todos", "todas", "all"}

T = TypeVar("T")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower().strip()
    for prefix in ("ar do ", "ar da ", "ar "):
        if text.startswith(prefix):
            text = text[len(prefix):]
    return text


def load_aliases(path: Path = CONFIG_PATH) -> dict[str, str]:
    try:
        return json.loads(path.read_text()).get("aliases", {})
    except (FileNotFoundError, ValueError):
        return {}


def save_alias(alias: str, mac: str, path: Path = CONFIG_PATH) -> None:
    try:
        config = json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        config = {}
    config.setdefault("aliases", {})[normalize(alias)] = mac
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2, ensure_ascii=False))


def resolve(query: str, devices: list[T], aliases: dict[str, str]) -> list[T]:
    """Aparelhos que correspondem ao nome pedido. Devices precisam de .name e .mac."""
    q = normalize(query)
    if q in ALL_WORDS:
        return list(devices)
    if q in aliases:
        found = [d for d in devices if d.mac == aliases[q]]
        if found:
            return found
    exact = [d for d in devices if normalize(d.name) == q]
    if exact:
        return exact
    partial = [d for d in devices if q in normalize(d.name)]
    if len(partial) == 1:
        return partial
    if not partial:
        raise LookupError(f"Nenhum aparelho chamado '{query}'")
    raise LookupError(f"'{query}' é ambíguo: " + ", ".join(d.name for d in partial))


def load_preferences(path: Path = CONFIG_PATH) -> dict[str, dict[str, str]]:
    """Preferências por aparelho (MAC), ex.: {"14-33-...": {"screenDisplayStatus": "0"}}."""
    try:
        return json.loads(path.read_text()).get("preferencias", {})
    except (FileNotFoundError, ValueError):
        return {}


def save_preference(mac: str, key: str, value: str, path: Path = CONFIG_PATH) -> None:
    try:
        config = json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        config = {}
    config.setdefault("preferencias", {}).setdefault(mac, {})[key] = str(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2, ensure_ascii=False))
