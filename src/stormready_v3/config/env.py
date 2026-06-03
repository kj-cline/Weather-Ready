from __future__ import annotations

import os
from pathlib import Path


def _workspace_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _parse_env_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if stripped.startswith("export "):
        stripped = stripped[len("export ") :].strip()
    if "=" not in stripped:
        return None
    key, value = stripped.split("=", 1)
    key = key.strip()
    value = value.strip()
    if not key:
        return None
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return key, value


def load_env_file(path: Path, *, override: bool = False) -> bool:
    if not path.exists():
        return False
    loaded = False
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        parsed = _parse_env_line(raw_line)
        if parsed is None:
            continue
        key, value = parsed
        if override or key not in os.environ:
            os.environ[key] = value
            loaded = True
    return loaded


def load_workspace_env(*, override: bool = False) -> list[str]:
    root = _workspace_root()
    loaded_paths: list[str] = []
    for name in (".env", ".env.local"):
        path = root / name
        if load_env_file(path, override=override):
            loaded_paths.append(str(path))
    return loaded_paths
