"""Outcome judge — calls an LLM with the policy text + trace + renders, returns Verdict.

Lazy-imports `anthropic`. If the SDK isn't installed or no API key is set, returns
Skip("LLM judge unavailable") so the runner can carry on.

Cache: keyed on (policy_id, trace_hash). Stored as JSON under evals/results/.cache/.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from ..framework import EVALS_ROOT, Fail, Pass, Policy, Skip, Trace, Verdict

CACHE_DIR = EVALS_ROOT / "results" / ".cache"

DEFAULT_MODEL = "claude-sonnet-4-5"

JUDGE_SYSTEM = """You are an evaluation judge for a 3D-modeling agent.

You will be given:
1. A POLICY in plain English describing a rule the agent must satisfy.
2. A TOOL TRACE — the sequence of tool calls the agent made.
3. SCENE STATE — the final geometry the agent produced (object names, dimensions, positions).
4. RENDERS — descriptions or paths to images of the final scene.

Your job is to decide whether the policy is satisfied. Be strict but fair:
- If the trace clearly violates the policy, return FAIL with a one-sentence reason.
- If the trace clearly satisfies the policy, return PASS.
- If the evidence is insufficient (e.g. you'd need a cross-section render that wasn't produced), return FAIL with reason "insufficient validation".

Respond as a single JSON object with keys: status ("pass" | "fail"), reason (string).
No other text.
"""


def _trace_hash(policy: Policy, trace: Trace) -> str:
    payload = json.dumps({
        "policy_id": policy.id,
        "policy_body": policy.body,
        "trace": trace.to_dict(),
    }, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _load_cached(key: str) -> Verdict | None:
    p = CACHE_DIR / f"{key}.json"
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    status = d.get("status")
    reason = d.get("reason", "")
    if status == "pass":
        return Pass(reason=reason)
    if status == "fail":
        return Fail(reason=reason)
    if status == "skip":
        return Skip(reason=reason)
    return None


def _save_cache(key: str, verdict: Verdict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / f"{key}.json").write_text(
        json.dumps({"status": verdict.status, "reason": getattr(verdict, "reason", "")}),
        encoding="utf-8",
    )


def _build_user_message(policy: Policy, trace: Trace,
                        scene_state: dict[str, Any], renders: list[dict[str, Any]]) -> str:
    return (
        f"## POLICY: {policy.id}\n\n{policy.body}\n\n"
        f"## TOOL TRACE\n\n```json\n{json.dumps(trace.to_dict()['calls'], indent=2, default=str)}\n```\n\n"
        f"## SCENE STATE\n\n```json\n{json.dumps(scene_state, indent=2, default=str)}\n```\n\n"
        f"## RENDERS\n\n{len(renders)} render(s) attached.\n"
    )


def judge(policy: Policy, trace: Trace,
          scene_state: dict[str, Any] | None = None,
          renders: list[dict[str, Any]] | None = None,
          model: str | None = None,
          use_cache: bool = True) -> Verdict:
    if policy.type != "outcome":
        return Skip(f"policy {policy.id} is type={policy.type}, not outcome")

    scene_state = scene_state or trace.scene_state
    renders = renders or trace.renders

    cache_key = _trace_hash(policy, trace)
    if use_cache:
        cached = _load_cached(cache_key)
        if cached is not None:
            return cached

    if not os.environ.get("ANTHROPIC_API_KEY"):
        return Skip("ANTHROPIC_API_KEY not set; skipping LLM judge")

    try:
        import anthropic  # type: ignore
    except ImportError:
        return Skip("`anthropic` SDK not installed; skipping LLM judge")

    client = anthropic.Anthropic()
    msg = _build_user_message(policy, trace, scene_state, renders)
    try:
        resp = client.messages.create(
            model=model or DEFAULT_MODEL,
            max_tokens=512,
            system=JUDGE_SYSTEM,
            messages=[{"role": "user", "content": msg}],
        )
    except Exception as e:
        return Fail(f"LLM judge call failed: {type(e).__name__}: {e}")

    text = "".join(
        block.text for block in resp.content if getattr(block, "type", None) == "text"
    ).strip()

    verdict = _parse_judge_response(text)
    if use_cache:
        _save_cache(cache_key, verdict)
    return verdict


def _parse_judge_response(text: str) -> Verdict:
    try:
        # Tolerate a leading code fence.
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
            cleaned = cleaned.strip()
        d = json.loads(cleaned)
    except json.JSONDecodeError:
        return Fail(f"judge response not parseable as JSON: {text[:200]!r}")

    status = (d.get("status") or "").lower()
    reason = d.get("reason", "")
    if status == "pass":
        return Pass(reason=reason)
    if status == "fail":
        return Fail(reason=reason or "judge returned fail with no reason")
    return Fail(f"judge returned unexpected status={status!r}")
