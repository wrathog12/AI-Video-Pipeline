"""Minimal .env loader.

Deliberately not python-dotenv: this is ~20 lines, and one fewer dependency in
the clean-machine install path is worth more than the extra features.

Real environment variables always win over the file, so CI and one-off overrides
(`GEMINI_API_KEY=... ./run ...`) behave as expected.
"""

from __future__ import annotations

import os
from pathlib import Path


def load_env(path: Path | None = None) -> list[str]:
    """Load KEY=VALUE lines from `.env` into os.environ.

    Returns the names of the keys that were set, so the caller can report what
    was picked up without ever logging a value.
    """
    env_path = Path(path) if path else Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return []

    loaded: list[str] = []
    for raw in env_path.read_text("utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # Strip matched surrounding quotes; a key pasted from a shell export
        # often carries them, and they are not part of the value.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if not key or not value:
            continue
        # Real env wins: never clobber something explicitly exported.
        if key in os.environ and os.environ[key]:
            continue
        os.environ[key] = value
        loaded.append(key)
    return loaded


def redact(value: str | None) -> str:
    """Render a secret for logs: presence and shape only, never the value."""
    if not value:
        return "(unset)"
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}…{value[-2:]} ({len(value)} chars)"
