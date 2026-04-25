"""Eval framework primitives — Verdict, ToolCall, Trace, policy/scenario loaders.

Pure stdlib. Imported by judges, runner, and any agent adapter.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent
EVALS_ROOT = REPO_ROOT / "evals"
POLICIES_DIR = EVALS_ROOT / "policies"
SCENARIOS_DIR = EVALS_ROOT / "scenarios"


# ---------------------------------------------------------------------------
# Verdicts (returned by judges)
# ---------------------------------------------------------------------------

@dataclass
class Pass:
    reason: str = ""
    status: str = "pass"

@dataclass
class Fail:
    reason: str
    status: str = "fail"

@dataclass
class Skip:
    reason: str
    status: str = "skip"


Verdict = Pass | Fail | Skip


# ---------------------------------------------------------------------------
# Tool call / trace records (what an agent adapter produces)
# ---------------------------------------------------------------------------

@dataclass
class ToolCall:
    call_index: int
    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] | None = None
    error: str | None = None

    @classmethod
    def from_dict(cls, d: dict, idx: int | None = None) -> "ToolCall":
        return cls(
            call_index=d.get("call_index", idx if idx is not None else 0),
            tool=d["tool"],
            args=d.get("args", {}),
            result=d.get("result"),
            error=d.get("error"),
        )


@dataclass
class Trace:
    """All tool calls + final scene state from a single agent run."""
    scenario_id: str
    agent: str
    calls: list[ToolCall]
    scene_state: dict[str, Any] = field(default_factory=dict)
    renders: list[dict[str, Any]] = field(default_factory=list)  # [{label, b64}, ...]
    transcript: str = ""

    def __iter__(self) -> Iterable[ToolCall]:
        return iter(self.calls)

    @classmethod
    def from_json(cls, path: Path) -> "Trace":
        d = json.loads(path.read_text(encoding="utf-8"))
        calls = [ToolCall.from_dict(c, i) for i, c in enumerate(d.get("calls", []))]
        return cls(
            scenario_id=d["scenario_id"],
            agent=d.get("agent", "unknown"),
            calls=calls,
            scene_state=d.get("scene_state", {}),
            renders=d.get("renders", []),
            transcript=d.get("transcript", ""),
        )

    def to_dict(self) -> dict:
        return {
            "scenario_id": self.scenario_id,
            "agent": self.agent,
            "calls": [asdict(c) for c in self.calls],
            "scene_state": self.scene_state,
            "renders": self.renders,
            "transcript": self.transcript,
        }


# ---------------------------------------------------------------------------
# Policy + Scenario file loaders
# ---------------------------------------------------------------------------

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)\Z", re.DOTALL)
CHECK_BLOCK_RE = re.compile(r"```python\s*\n(.*?)\n```", re.DOTALL)


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Minimal YAML-ish frontmatter parser.

    Supports: scalar `key: value`, list `key: [a, b]`, nested mapping
    via 2-space indent (one level deep), and YAML list-of-scalars form
    (`key:\n  - a\n  - b`). Good enough for the small set of fields we use.
    """
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    raw, body = m.group(1), m.group(2)

    data: dict[str, Any] = {}
    current_key: str | None = None
    nested: dict[str, Any] | None = None
    list_buffer: list[Any] | None = None

    def coerce(v: str) -> Any:
        v = v.strip()
        if v.startswith("[") and v.endswith("]"):
            return [x.strip().strip('"\'') for x in v[1:-1].split(",") if x.strip()]
        if v.lower() in ("true", "false"):
            return v.lower() == "true"
        try:
            if "." in v:
                return float(v)
            return int(v)
        except ValueError:
            return v.strip('"\'')

    for line in raw.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        # Indented lines are part of nested mapping or list under current_key
        if line.startswith("  "):
            stripped = line.strip()
            if stripped.startswith("- "):
                if list_buffer is None:
                    list_buffer = []
                    data[current_key] = list_buffer
                list_buffer.append(coerce(stripped[2:]))
                continue
            if ":" in stripped:
                k, v = stripped.split(":", 1)
                if nested is None:
                    nested = {}
                    data[current_key] = nested
                nested[k.strip()] = coerce(v)
                continue
        # Top-level
        nested = None
        list_buffer = None
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        current_key = k.strip()
        v = v.strip()
        if not v:
            data[current_key] = None  # placeholder for nested or list
        else:
            data[current_key] = coerce(v)

    return data, body


@dataclass
class Policy:
    id: str
    type: str  # "procedural" | "outcome"
    applies_to: list[str]
    severity: str
    body: str
    check_source: str | None  # python source for procedural type, or None
    path: Path

    @classmethod
    def load(cls, path: Path) -> "Policy":
        text = path.read_text(encoding="utf-8")
        meta, body = _parse_frontmatter(text)
        check_match = CHECK_BLOCK_RE.search(body)
        applies_to = meta.get("applies_to", [])
        if isinstance(applies_to, str):
            applies_to = [applies_to]
        return cls(
            id=meta["id"],
            type=meta.get("type", "outcome"),
            applies_to=applies_to,
            severity=meta.get("severity", "medium"),
            body=body,
            check_source=check_match.group(1) if check_match else None,
            path=path,
        )

    def applies_to_scenario(self, scenario_id: str) -> bool:
        return "*" in self.applies_to or scenario_id in self.applies_to


@dataclass
class Scenario:
    id: str
    prompt: str
    applies_policies: list[str]
    budget: dict[str, Any]
    body: str
    path: Path

    @classmethod
    def load(cls, path: Path) -> "Scenario":
        text = path.read_text(encoding="utf-8")
        meta, body = _parse_frontmatter(text)
        applies = meta.get("applies_policies", [])
        if isinstance(applies, str):
            applies = [applies]
        budget = meta.get("budget", {}) or {}
        return cls(
            id=meta["id"],
            prompt=meta.get("prompt", ""),
            applies_policies=applies,
            budget=budget,
            body=body,
            path=path,
        )


def load_all_policies() -> list[Policy]:
    return sorted(
        [Policy.load(p) for p in POLICIES_DIR.glob("*.md")],
        key=lambda p: p.id,
    )


def load_all_scenarios() -> list[Scenario]:
    return sorted(
        [Scenario.load(p) for p in SCENARIOS_DIR.glob("*.md")],
        key=lambda s: s.id,
    )
