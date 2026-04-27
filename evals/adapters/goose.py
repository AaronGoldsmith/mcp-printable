"""Goose adapter — runs a scenario via a Goose recipe and captures the trace.

Approach:
  1. `goose run --recipe ... -n <session_name>` — Goose persists the full
     structured conversation to its sessions store.
  2. After the subprocess exits, `goose session export -n <name> --format json`
     emits the full session payload (top-level metadata + a `conversation`
     array of MessageContent blocks). We parse that and build a Trace.

We use the export CLI rather than reading Goose's internal SQLite directly so
the adapter rides the documented public boundary and survives schema changes.
This also avoids parsing the streaming-JSONL stdout, which fragments text into
many partial chunks.

References (verified 2026-04):
  Recipe / CLI flags:    https://block.github.io/goose/docs/
  `session export` CLI:  goose session export --help
  MessageContent enum:   crates/goose/src/conversation/message.rs
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..framework import EVALS_ROOT, REPO_ROOT, Scenario, ToolCall, Trace

DEFAULT_RECIPE = EVALS_ROOT / "goose-recipes" / "blender.yaml"


@dataclass
class GooseRun:
    provider: str = "anthropic"
    model: str = "claude-sonnet-4-5"
    recipe: Path = DEFAULT_RECIPE
    max_turns: int = 80
    max_tool_repetitions: int = 5
    timeout_s: int = 600
    debug: bool = False
    extension_name: str = "printable"  # must match `name:` in the recipe

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

    session_name = f"eval_{scenario.id}_{cfg.model.replace('/', '_')}"
    cmd = [
        "goose", "run",
        "--recipe", str(cfg.recipe),
        "-n", session_name,
        "--max-turns", str(scenario.budget.get("max_tool_calls", cfg.max_turns)),
        "--max-tool-repetitions", str(cfg.max_tool_repetitions),
    ]
    if cfg.debug:
        cmd.append("--debug")
    else:
        cmd.append("-q")
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
        encoding="utf-8", errors="replace",
        timeout=timeout, cwd=str(REPO_ROOT),
    )

    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)
        tag = f"{scenario.id}_{cfg.model.replace('/', '_')}"
        if proc.stderr:
            (log_dir / f"{tag}.stderr.txt").write_text(proc.stderr, encoding="utf-8")

    return load_trace_from_session(session_name, scenario.id,
                                   extension_prefix=cfg.extension_name)


def load_trace_from_session(session_name: str, scenario_id: str,
                            extension_prefix: str = "printable") -> Trace:
    """Export the named Goose session and build a Trace from it.

    Calls `goose session export -n <session_name> --format json` and parses
    the resulting payload — the documented public surface for getting a
    structured copy of a session's conversation.

    The export schema (top-level keys we use): `provider_name`, `model_config`,
    `conversation`. Each conversation entry has `role` and `content` (a list
    of MessageContent blocks: toolRequest, toolResponse, text, ...).
    """
    proc = subprocess.run(
        ["goose", "session", "export", "-n", session_name, "--format", "json"],
        capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        raise RuntimeError(
            f"`goose session export -n {session_name}` failed "
            f"(rc={proc.returncode}): {proc.stderr.strip() or '<no stderr>'}"
        )

    data = json.loads(proc.stdout)
    conversation = data.get("conversation") or []
    messages = [(m.get("role"), m.get("content") or []) for m in conversation]

    calls = _messages_to_calls(messages, extension_prefix=extension_prefix)
    transcript = _messages_to_transcript(messages)

    provider_name = data.get("provider_name") or ""
    model_cfg = data.get("model_config") or {}
    model = model_cfg.get("model_name") or model_cfg.get("model") or ""

    return Trace(
        scenario_id=scenario_id,
        agent=f"goose:{provider_name}/{model}",
        calls=calls,
        transcript=transcript,
    )


def _messages_to_calls(messages: list[tuple[str, list]],
                       extension_prefix: str = "") -> list[ToolCall]:
    """Walk (role, content_blocks) pairs from a session export; extract ToolCalls.

    Goose's MessageContent enum (camelCase variants from
    crates/goose/src/conversation/message.rs):
        {"type": "toolRequest",  "id": str,
         "toolCall":   {"status": "success", "value": {"name": str, "arguments": {...}}}}
        {"type": "toolResponse", "id": str,
         "toolResult": {"status": "success", "value": {"content": [...]}}}
    """
    calls: list[ToolCall] = []
    by_id: dict[str, int] = {}
    prefix = f"{extension_prefix}__" if extension_prefix else ""

    for _role, blocks in messages:
        for blk in blocks or []:
            if not isinstance(blk, dict):
                continue
            t = blk.get("type")

            if t == "toolRequest":
                tid = blk.get("id") or f"_anon_{len(calls)}"
                tc = blk.get("toolCall") or {}
                if tc.get("status") != "success":
                    idx = len(calls)
                    calls.append(ToolCall(
                        call_index=idx, tool="<malformed>", args={},
                        error=tc.get("error") or "malformed tool call",
                    ))
                    by_id[tid] = idx
                    continue
                val = tc.get("value") or {}
                name = val.get("name", "<unknown>")
                if prefix and name.startswith(prefix):
                    name = name[len(prefix):]
                idx = len(calls)
                calls.append(ToolCall(
                    call_index=idx, tool=name, args=val.get("arguments") or {},
                ))
                by_id[tid] = idx

            elif t == "toolResponse":
                tid = blk.get("id")
                if tid not in by_id:
                    continue
                idx = by_id[tid]
                tr = blk.get("toolResult") or {}
                if tr.get("status") == "success":
                    calls[idx].result = _flatten_call_tool_result(tr.get("value") or {})
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


def _messages_to_transcript(messages: list[tuple[str, list]]) -> str:
    """Join assistant text + reasoning blocks into a single transcript.

    Reasoning is included so LLM judges can see *why* the agent chose a tool
    sequence, not just the final summary. It's prefixed with `[reasoning]` so
    consumers can strip it with a regex if they only want user-visible text.

    Tool-only assistant turns (toolRequest with no text/reasoning) emit nothing.
    """
    chunks: list[str] = []
    for role, blocks in messages:
        if role != "assistant":
            continue
        for blk in blocks or []:
            if not isinstance(blk, dict):
                continue
            t = blk.get("type")
            if t == "text":
                txt = blk.get("text", "").strip()
                if txt:
                    chunks.append(txt)
            elif t == "reasoning":
                txt = blk.get("text", "").strip()
                if txt:
                    chunks.append(f"[reasoning] {txt}")
    return "\n\n".join(chunks)
