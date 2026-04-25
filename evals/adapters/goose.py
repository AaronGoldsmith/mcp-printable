"""Goose adapter — runs a scenario via a Goose recipe and captures the trace.

Goose has built-in machinery for everything we'd otherwise plumb by hand:
extension wiring, model selection, parameter substitution. We use it.

  evals/recipes/blender.yaml — declares MCPs, builtins, model. Parameterized
  on `scenario_prompt`, `provider`, `model`, `printable_server_path`.

This adapter just:
  1. Renders the recipe params for a given scenario.
  2. Invokes `goose run --recipe ... --output-format stream-json`.
  3. Parses the JSONL event stream into a Trace.

References (verified 2026-04):
  https://block.github.io/goose/docs/  (recipe schema, --params, --output-format)
  Repo: https://github.com/aaif-goose/goose
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..framework import EVALS_ROOT, REPO_ROOT, Scenario, ToolCall, Trace

DEFAULT_RECIPE = EVALS_ROOT / "recipes" / "blender.yaml"


@dataclass
class GooseRun:
    provider: str = "anthropic"
    model: str = "claude-sonnet-4-5"
    recipe: Path = DEFAULT_RECIPE
    max_turns: int = 80
    max_tool_repetitions: int = 5
    timeout_s: int = 600

    @property
    def label(self) -> str:
        return f"goose:{self.provider}/{self.model}"


class GooseNotInstalled(RuntimeError):
    pass


def _check_goose() -> None:
    if not shutil.which("goose"):
        raise GooseNotInstalled(
            "`goose` not on PATH. Install: https://block.github.io/goose/"
        )


def run(scenario: Scenario, cfg: GooseRun | None = None,
        log_dir: Path | None = None) -> Trace:
    """Execute one scenario via Goose; return the resulting Trace."""
    cfg = cfg or GooseRun()
    _check_goose()
    if not cfg.recipe.exists():
        raise FileNotFoundError(f"recipe not found: {cfg.recipe}")

    server_path = (REPO_ROOT / "server.py").as_posix()
    params = [
        f"scenario_prompt={scenario.prompt}",
        f"provider={cfg.provider}",
        f"model={cfg.model}",
        f"printable_server_path={server_path}",
    ]

    cmd = [
        "goose", "run",
        "--recipe", str(cfg.recipe),
        "--max-turns", str(scenario.budget.get("max_tool_calls", cfg.max_turns)),
        "--max-tool-repetitions", str(cfg.max_tool_repetitions),
        "--output-format", "stream-json",
        "--no-session",
        "-q",
    ]
    for p in params:
        cmd += ["--params", p]

    timeout = scenario.budget.get("max_wall_seconds", cfg.timeout_s)
    env = {**os.environ, "GOOSE_MODE": "auto"}
    # Gemini 3 reasoning is on by default and slow; default to "low" for evals.
    # User can override by setting GEMINI3_THINKING_LEVEL in their shell.
    if "gemini" in cfg.provider.lower() or "gemini" in cfg.model.lower():
        env.setdefault("GEMINI3_THINKING_LEVEL", "low")

    proc = subprocess.run(
        cmd, env=env, capture_output=True, text=True,
        timeout=timeout, cwd=str(REPO_ROOT),
    )

    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)
        tag = f"{scenario.id}_{cfg.model.replace('/', '_')}"
        (log_dir / f"{tag}.stdout.jsonl").write_text(proc.stdout, encoding="utf-8")
        if proc.stderr:
            (log_dir / f"{tag}.stderr.txt").write_text(proc.stderr, encoding="utf-8")

    events = _parse_jsonl(proc.stdout)
    calls = _events_to_calls(events)
    transcript = _extract_transcript(events)

    return Trace(
        scenario_id=scenario.id,
        agent=f"goose:{cfg.provider}/{cfg.model}",
        calls=calls,
        transcript=transcript,
    )


def _parse_jsonl(blob: str) -> list[dict]:
    out: list[dict] = []
    for line in blob.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _events_to_calls(events: list[dict]) -> list[ToolCall]:
    """Parse Goose stream-json events into ToolCall records.

    The schema is model-agnostic — Goose normalizes provider responses (Anthropic
    tool_use, OpenAI tool_calls, Gemini function_call) into its internal Message
    type before serializing. Verified against:
      crates/goose-cli/src/session/mod.rs       (StreamEvent, tag="type")
      crates/goose/src/conversation/message.rs  (MessageContent, tag="type", camelCase)

    Top-level event shape:
        {"type": "message", "message": {"role": "...", "content": [...blocks...]}}
        {"type": "notification" | "error" | "complete", ...}

    Block shapes we care about (camelCase tags from MessageContent variants):
        {"type": "toolRequest",  "id": str,
         "toolCall":   {"status": "success", "value": {"name": str, "arguments": {...}}}}
        {"type": "toolResponse", "id": str,
         "toolResult": {"status": "success", "value": {"content": [...]}}}
        Either toolCall.status or toolResult.status may be "error" with an "error" field.
    """
    calls: list[ToolCall] = []
    by_id: dict[str, int] = {}

    for ev in events:
        if ev.get("type") != "message":
            continue
        msg = ev.get("message") or {}
        for blk in msg.get("content") or []:
            if not isinstance(blk, dict):
                continue
            t = blk.get("type")

            if t == "toolRequest":
                tid = blk.get("id") or f"_anon_{len(calls)}"
                tc = blk.get("toolCall") or {}
                if tc.get("status") == "success":
                    val = tc.get("value") or {}
                    name = val.get("name", "<unknown>")
                    args = val.get("arguments") or {}
                    idx = len(calls)
                    calls.append(ToolCall(call_index=idx, tool=name, args=args))
                    by_id[tid] = idx
                else:
                    # The model produced an unparseable tool call — record it as an error.
                    idx = len(calls)
                    calls.append(ToolCall(
                        call_index=idx, tool="<malformed>", args={},
                        error=tc.get("error") or "malformed tool call",
                    ))
                    by_id[tid] = idx

            elif t == "toolResponse":
                tid = blk.get("id")
                if tid not in by_id:
                    continue
                idx = by_id[tid]
                tr = blk.get("toolResult") or {}
                if tr.get("status") == "success":
                    val = tr.get("value") or {}
                    calls[idx].result = _flatten_call_tool_result(val)
                else:
                    calls[idx].error = tr.get("error") or "tool error"

    return calls


def _flatten_call_tool_result(value: dict) -> dict[str, Any]:
    """Extract usable result data from MCP CallToolResult.

    Shape: {"content": [{"type": "text", "text": "..."}, ...], "isError": bool}.
    We pull text payloads (often JSON) and json-decode them when possible.
    """
    if not isinstance(value, dict):
        return {"value": value}
    parts: list[Any] = []
    for c in value.get("content") or []:
        if isinstance(c, dict) and c.get("type") == "text":
            text = c.get("text", "")
            try:
                parts.append(json.loads(text))
            except (json.JSONDecodeError, TypeError):
                parts.append(text)
        else:
            parts.append(c)
    if len(parts) == 1 and isinstance(parts[0], dict):
        return parts[0]
    return {"content": parts}


def _extract_transcript(events: list[dict]) -> str:
    chunks: list[str] = []
    for ev in events:
        if ev.get("type") != "message":
            continue
        msg = ev.get("message") or {}
        if msg.get("role") != "assistant":
            continue
        for blk in msg.get("content") or []:
            if isinstance(blk, dict) and blk.get("type") == "text":
                chunks.append(blk.get("text", ""))
    return "\n\n".join(c for c in chunks if c)
