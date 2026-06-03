"""Parse agent policy markdown files into ``AgentPolicy`` objects.

Policy files live in ``agents/policies/<role>.md`` and use YAML-style frontmatter
for structured fields plus a free-form markdown body that becomes the system
prompt. Parsing is deliberately minimal: no external YAML dependency, no nested
structures. Keys are either scalar ``key: value`` or list

    key:
      - item
      - item

Any parse error raises at boot, mirroring the provider fail-fast pattern in
``ai/factory.py``. A malformed policy must not produce a half-valid agent.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import AgentPolicy, AgentRole


POLICY_DIR = Path(__file__).parent / "policies"


class PolicyLoadError(ValueError):
    pass


def load_policy(role: AgentRole, policy_dir: Path | None = None) -> AgentPolicy:
    base_dir = policy_dir or POLICY_DIR
    path = base_dir / f"{role.value}.md"
    if not path.is_file():
        raise PolicyLoadError(
            f"policy file not found for role {role.value}: {path}"
        )
    text = path.read_text(encoding="utf-8")
    frontmatter, body = _split_frontmatter(text, path)
    meta = _parse_frontmatter(frontmatter, path)
    try:
        policy_role = AgentRole(meta["role"])
    except KeyError as exc:
        raise PolicyLoadError(f"{path}: missing required 'role' field") from exc
    except ValueError as exc:
        raise PolicyLoadError(f"{path}: unknown role {meta['role']!r}") from exc

    if policy_role != role:
        raise PolicyLoadError(
            f"{path}: frontmatter role {policy_role.value!r} does not match requested role {role.value!r}"
        )

    try:
        return AgentPolicy(
            role=policy_role,
            version=int(meta["version"]),
            description=_as_str(meta.get("description", "")),
            trigger=_as_str(meta.get("trigger", "")),
            allowed_writes=_as_tuple(meta.get("allowed_writes", ())),
            forbidden_writes=_as_tuple(meta.get("forbidden_writes", ())),
            allowed_categories=_as_tuple(meta.get("allowed_categories", ())),
            forbidden_source_classes=_as_tuple(meta.get("forbidden_source_classes", ())),
            max_outputs_per_run=int(meta.get("max_outputs_per_run", 5)),
            max_tokens=int(meta.get("max_tokens", 800)),
            tier1_max_strength_per_signal=float(
                meta.get("tier1_max_strength_per_signal", 0.05)
            ),
            tier1_max_strength_total=float(
                meta.get("tier1_max_strength_total", 0.15)
            ),
            requires_confirmation_when=_as_tuple(
                meta.get("requires_confirmation_when", ())
            ),
            system_prompt_body=body.strip(),
        )
    except (KeyError, ValueError, TypeError) as exc:
        raise PolicyLoadError(f"{path}: invalid field value: {exc}") from exc


def _split_frontmatter(text: str, path: Path) -> tuple[str, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise PolicyLoadError(
            f"{path}: policy must begin with '---' frontmatter marker"
        )
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            return "\n".join(lines[1:idx]), "\n".join(lines[idx + 1 :])
    raise PolicyLoadError(f"{path}: unterminated frontmatter block")


def _parse_frontmatter(text: str, path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {}
    current_key: str | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- "):
            if current_key is None:
                raise PolicyLoadError(f"{path}: list item before any key")
            item = stripped[2:].strip()
            existing = result.get(current_key)
            if not isinstance(existing, list):
                result[current_key] = []
            result[current_key].append(_strip_quotes(item))
            continue
        if ":" not in line:
            raise PolicyLoadError(f"{path}: unparseable line: {raw!r}")
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        current_key = key
        if value == "":
            result[key] = []
        else:
            result[key] = _strip_quotes(value)
    return result


def _strip_quotes(s: str) -> str:
    if len(s) >= 2 and s[0] == s[-1] and s[0] in {'"', "'"}:
        return s[1:-1]
    return s


def _as_str(value: Any) -> str:
    if isinstance(value, list):
        return " ".join(str(v) for v in value)
    return str(value)


def _as_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(str(v) for v in value)
    if isinstance(value, str):
        return (value,) if value else ()
    raise TypeError(f"expected list or string, got {type(value).__name__}")


__all__ = ["load_policy", "PolicyLoadError", "POLICY_DIR"]
