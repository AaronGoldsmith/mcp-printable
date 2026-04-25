"""Eval runner — load policies + scenarios, dispatch via an adapter, judge, report.

Usage:
  python -m evals.runner --self-test                       # mock adapter, no Goose needed
  python -m evals.runner                                   # all scenarios via Goose, default model
  python -m evals.runner --scenario simple-cube
  python -m evals.runner --model claude-opus-4-7
  python -m evals.runner --policy always-clear-scene       # only judge this policy
  python -m evals.runner --write-baseline                  # snapshot pass/fail state
  python -m evals.runner --compare results/baseline.json   # diff against baseline
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .framework import (
    EVALS_ROOT,
    Fail,
    Pass,
    Policy,
    Scenario,
    Skip,
    Trace,
    Verdict,
    load_all_policies,
    load_all_scenarios,
)
from .judges import outcome_llm, procedural

RESULTS_DIR = EVALS_ROOT / "results"


# ---------------------------------------------------------------------------
# Adapter dispatch
# ---------------------------------------------------------------------------

def _dispatch_trace(scenario: Scenario, agent: str, provider: str, model: str,
                    log_dir: Path | None) -> Trace:
    if agent == "mock":
        from .adapters import mock
        return mock.run(scenario.prompt, scenario.id, variant="good")
    if agent == "mock-bad":
        from .adapters import mock
        return mock.run(scenario.prompt, scenario.id, variant="bad")
    if agent == "goose":
        from .adapters import goose
        cfg = goose.GooseRun(provider=provider, model=model)
        return goose.run(scenario, cfg, log_dir=log_dir)
    raise ValueError(f"unknown agent: {agent!r}")


# ---------------------------------------------------------------------------
# Judge dispatch
# ---------------------------------------------------------------------------

def _judge(policy: Policy, trace: Trace) -> Verdict:
    if policy.type == "procedural":
        return procedural.judge(policy, trace)
    if policy.type == "outcome":
        return outcome_llm.judge(policy, trace)
    return Skip(f"unknown policy type {policy.type!r}")


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

GLYPH = {"pass": "PASS", "fail": "FAIL", "skip": "SKIP"}


def _print_grid(results: list[dict]) -> None:
    if not results:
        print("(no results)")
        return
    width_s = max(len(r["scenario"]) for r in results)
    width_p = max(len(r["policy"]) for r in results)
    print(f"{'SCENARIO'.ljust(width_s)}  {'POLICY'.ljust(width_p)}  STATUS  REASON")
    print("-" * (width_s + width_p + 24))
    for r in results:
        glyph = GLYPH.get(r["status"], r["status"].upper())
        reason = (r["reason"] or "")[:80]
        print(f"{r['scenario'].ljust(width_s)}  {r['policy'].ljust(width_p)}  {glyph:6}  {reason}")


def _to_record(scenario: Scenario, policy: Policy, verdict: Verdict, trace: Trace) -> dict:
    return {
        "scenario": scenario.id,
        "policy": policy.id,
        "policy_type": policy.type,
        "severity": policy.severity,
        "status": verdict.status,
        "reason": getattr(verdict, "reason", ""),
        "agent": trace.agent,
        "n_calls": len(trace.calls),
    }


def _diff_against_baseline(latest: list[dict], baseline_path: Path) -> tuple[list[dict], int]:
    if not baseline_path.exists():
        print(f"baseline not found: {baseline_path}")
        return [], 0
    baseline = {(r["scenario"], r["policy"]): r for r in json.loads(
        baseline_path.read_text(encoding="utf-8"))}
    regressions: list[dict] = []
    for r in latest:
        prev = baseline.get((r["scenario"], r["policy"]))
        if prev is None:
            continue
        if prev["status"] == "pass" and r["status"] == "fail":
            regressions.append({"scenario": r["scenario"], "policy": r["policy"],
                                "was": prev["status"], "now": r["status"],
                                "reason": r["reason"]})
    return regressions, len(regressions)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--scenario", help="run only this scenario id")
    p.add_argument("--policy", help="judge only this policy id")
    p.add_argument("--agent", default="goose",
                   choices=["goose", "mock", "mock-bad"])
    p.add_argument("--provider", default="anthropic",
                   help="Goose provider: anthropic | gemini_oauth | openrouter | ...")
    p.add_argument("--model", default="claude-sonnet-4-5")
    p.add_argument("--self-test", action="store_true",
                   help="shortcut: --agent mock and run the procedural judge "
                        "against the canned good and bad mock traces")
    p.add_argument("--write-baseline", action="store_true")
    p.add_argument("--compare", help="path to baseline.json")
    p.add_argument("--log-dir", default=str(RESULTS_DIR / "logs"),
                   help="where to write per-run agent logs")
    args = p.parse_args(argv)

    if args.self_test:
        return _run_self_test()

    scenarios = load_all_scenarios()
    policies = load_all_policies()
    if args.scenario:
        scenarios = [s for s in scenarios if s.id == args.scenario]
        if not scenarios:
            print(f"no scenario with id {args.scenario!r}")
            return 2
    if args.policy:
        policies = [p_ for p_ in policies if p_.id == args.policy]
        if not policies:
            print(f"no policy with id {args.policy!r}")
            return 2

    log_dir = Path(args.log_dir) if args.log_dir else None
    results: list[dict] = []

    for scenario in scenarios:
        try:
            trace = _dispatch_trace(scenario, args.agent, args.provider,
                                    args.model, log_dir)
        except Exception as e:
            print(f"[{scenario.id}] adapter failed: {type(e).__name__}: {e}")
            for policy in policies:
                if not policy.applies_to_scenario(scenario.id):
                    continue
                if policy.id not in (scenario.applies_policies or [policy.id]):
                    if scenario.applies_policies:
                        continue
                results.append({
                    "scenario": scenario.id, "policy": policy.id,
                    "policy_type": policy.type, "severity": policy.severity,
                    "status": "fail", "reason": f"adapter error: {e}",
                    "agent": args.agent, "n_calls": 0,
                })
            continue

        for policy in policies:
            if not policy.applies_to_scenario(scenario.id):
                continue
            if scenario.applies_policies and policy.id not in scenario.applies_policies:
                continue
            verdict = _judge(policy, trace)
            results.append(_to_record(scenario, policy, verdict, trace))

    _print_grid(results)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    latest_path = RESULTS_DIR / "latest.json"
    latest_path.write_text(json.dumps(results, indent=2), encoding="utf-8")

    if args.write_baseline:
        baseline_path = RESULTS_DIR / "baseline.json"
        baseline_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"\nbaseline written: {baseline_path}")

    if args.compare:
        regressions, n = _diff_against_baseline(results, Path(args.compare))
        if n:
            print(f"\n{n} REGRESSION(S) vs baseline:")
            for r in regressions:
                print(f"  {r['scenario']}/{r['policy']}: {r['was']} -> {r['now']} ({r['reason']})")
            return 1
        print("\nNo regressions vs baseline.")

    n_fail = sum(1 for r in results if r["status"] == "fail")
    return 1 if n_fail else 0


# ---------------------------------------------------------------------------
# Self-test — exercises judges against canned mock traces, no agent needed
# ---------------------------------------------------------------------------

def _run_self_test() -> int:
    from .adapters import mock as mock_adapter

    print("=== SELF-TEST: judge plumbing on canned mock traces ===\n")
    cases = [
        ("simple-cube", "good", "always-clear-scene", "pass"),
        ("simple-cube", "bad", "always-clear-scene", "fail"),
        ("cube-with-hole", "good", "prefer-typed-boolean", "pass"),
        ("cube-with-hole", "bad", "prefer-typed-boolean", "fail"),
    ]
    policies = {p.id: p for p in load_all_policies()}
    failures = 0
    for scenario_id, variant, policy_id, expected in cases:
        trace = mock_adapter.run("", scenario_id, variant=variant)
        policy = policies[policy_id]
        verdict = _judge(policy, trace)
        ok = (verdict.status == expected)
        marker = "OK  " if ok else "FAIL"
        print(f"  [{marker}] {scenario_id}/{variant} vs {policy_id}: "
              f"got {verdict.status} (expected {expected}) "
              f"-- {getattr(verdict, 'reason', '')}")
        if not ok:
            failures += 1
    print()
    if failures:
        print(f"SELF-TEST FAILED ({failures}/{len(cases)})")
        return 1
    print(f"SELF-TEST PASSED ({len(cases)}/{len(cases)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
