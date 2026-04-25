"""Procedural judge — runs the python check() embedded in a procedural policy.

The policy's body contains a fenced ```python code block that defines a `check`
function with signature:

    check(trace, scene_state, renders) -> Pass | Fail | Skip

We exec() that code in a controlled namespace where Pass/Fail/Skip and `re` are
already bound, then call check() with the inputs.

Any exception from the check itself is treated as a Fail — a broken policy
shouldn't silently masquerade as a pass.
"""

from __future__ import annotations

import re
import traceback
from typing import Any

from ..framework import Fail, Pass, Policy, Skip, Trace, Verdict


class PolicyExecutionError(RuntimeError):
    pass


def _build_namespace() -> dict[str, Any]:
    return {
        "Pass": Pass,
        "Fail": Fail,
        "Skip": Skip,
        "re": re,
    }


def judge(policy: Policy, trace: Trace,
          scene_state: dict[str, Any] | None = None,
          renders: list[dict[str, Any]] | None = None) -> Verdict:
    if policy.type != "procedural":
        return Skip(f"policy {policy.id} is type={policy.type}, not procedural")
    if not policy.check_source:
        return Fail(f"procedural policy {policy.id} has no ```python check block")

    ns = _build_namespace()
    try:
        exec(compile(policy.check_source, str(policy.path), "exec"), ns)
    except Exception as e:
        return Fail(f"policy {policy.id} failed to compile: {e}")

    check_fn = ns.get("check")
    if not callable(check_fn):
        return Fail(f"policy {policy.id} did not define a callable `check`")

    try:
        verdict = check_fn(trace, scene_state or trace.scene_state,
                           renders or trace.renders)
    except Exception as e:
        tb = traceback.format_exc(limit=5)
        return Fail(f"policy {policy.id} check raised {type(e).__name__}: {e}\n{tb}")

    if not isinstance(verdict, (Pass, Fail, Skip)):
        return Fail(
            f"policy {policy.id} check returned {type(verdict).__name__}, "
            f"expected Pass | Fail | Skip"
        )
    return verdict
