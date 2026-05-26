# Evals

Regression tests for agent behavior. Verifies that agents using the Printable MCP actually follow the rules encoded in `docs/` and the MCP server's `instructions` field — not just that the tool surface works.

## The premise

The MCP server tells agents to do things like *"start every modeling task with `blender_clear_scene`"* and *"every moving part needs a continuous print path to bed"*. Whether an agent **actually** does this is an empirical question. Prose rules are non-deterministic. Models drift. New models behave differently.

This eval suite catches that drift. Same evals run against any MCP-capable agent (Claude, Goose, Cursor, …), so we can compare and detect regressions when the rules or the underlying model change.

## Two-tier judging

### Procedural policies — deterministic
Things checkable from the tool-call trace alone. No LLM judge. Zero flakiness.

> "First Blender tool call must be `blender_clear_scene`."

> "No raw `bpy.ops.object.modifier_apply` boolean modifiers inside `blender_execute_code` — must use `blender_boolean`."

### Outcome policies — LLM judge
Things that require interpreting the geometry. The judge gets the policy text, the tool trace, the final scene state, and the renders, then returns PASS/FAIL with reasoning.

> "Every moving part has a continuous, uninterrupted print path from the build plate to itself, not passing through the static part it moves against."

Some outcome-flavored policies can be reduced to deterministic checks via the MCP's existing validators (`blender_validate` with `checks=['OVERHANGS']` or `['ALL']`). Prefer that — cheaper, more reliable.

## Layout

```
evals/
├── README.md                   # this file
├── policies/                   # plain-English rules
│   ├── always-clear-scene.md           (procedural)
│   ├── prefer-typed-boolean.md         (procedural)
│   └── print-in-place-path-traceable.md (outcome)
├── scenarios/                  # prompts to feed the agent
│   ├── simple-cube.md
│   ├── cube-with-hole.md
│   └── basic-hinge.md
├── judges/
│   ├── procedural.py           # tool-trace checks
│   └── outcome_llm.py          # LLM judge wrapper
├── runner.py                   # spawns agent, captures trace, invokes judges
├── results/
│   ├── baseline.json           # known-good pass/fail snapshot
│   └── latest.json             # most recent run
└── adapters/                   # per-agent runners (claude_code.py, goose.py, ...)
```

## Policy format

```markdown
---
id: always-clear-scene
type: procedural               # procedural | outcome
applies_to: ["*"]              # scenario IDs or "*" for all
severity: critical             # critical | high | medium | low
---

# Always clear scene first

Plain-English rule. The judge sees this verbatim if it's an outcome policy.

## Pass criteria
Itemized criteria. For procedural policies, written so they map cleanly to a Python check. For outcome policies, written for an LLM to interpret.

## Fail criteria
What looks like a failure.

## Programmatic check (procedural only)
\`\`\`python
def check(trace, scene_state, renders):
    blender_calls = [c for c in trace if c.tool.startswith("blender_")]
    if not blender_calls:
        return Skip("no blender calls")
    return Pass() if blender_calls[0].tool == "blender_clear_scene" else Fail(
        f"first blender call was {blender_calls[0].tool}"
    )
\`\`\`
```

The procedural check is optional — if absent for a `procedural` policy the runner emits an error so policies don't silently skip.

## Scenario format

```markdown
---
id: basic-hinge
prompt: "Build me a parametric hinge with a 5mm barrel and two 20×15mm flanges."
applies_policies: ["always-clear-scene", "prefer-typed-boolean", "print-in-place-path-traceable"]
budget:
  max_tool_calls: 100
  max_wall_seconds: 600
---

# Basic hinge scenario

Notes for humans reviewing eval results: what the prompt is testing, what passes/fails are expected to look like, edge cases the agent might trip on.
```

## Running

```bash
# Run all scenarios against the default agent (Claude Code)
python evals/runner.py

# Specific scenario, specific agent
python evals/runner.py --scenario basic-hinge --agent goose

# Compare to baseline
python evals/runner.py --compare results/baseline.json

# Snapshot a new baseline (only after manual verification of current pass set)
python evals/runner.py --write-baseline
```

Output is a per-policy/per-scenario PASS/FAIL grid with reasoning, plus a regression diff against baseline. Exit code is non-zero if any policy regressed.

## Adding policies and scenarios

1. Drop a markdown file in `policies/` with frontmatter as above.
2. For procedural policies, include the Python check (or import it from `judges/procedural_lib.py`).
3. Drop a scenario in `scenarios/` if the policy needs a new context to be tested. Reuse existing scenarios when possible.
4. Run `python evals/runner.py --policy <new-policy-id>` to confirm it triggers correctly on a deliberate failure.
5. Run with `--write-baseline` once you're happy with where it lands.

## Status

This is a scaffold. The runner currently exercises the policy/judge plumbing on hand-crafted traces (see `runner.py --self-test`). Wiring real agents into `adapters/` is the next slice.

## Cost

LLM judges are the only meaningful cost. Procedural checks are free. The runner caches judge calls keyed on `(policy_id, trace_hash)` so re-running over an unchanged trace is free.

## Why this format and not pytest / a framework

- **Plain markdown is readable** by both humans and the LLMs that judge with it. Pytest hides the policy in code; here the policy IS the source of truth.
- **Frontmatter for triage**, body for prose. Same shape as Claude skills and `block/ai-rules`, so policies can move between systems without rewriting.
- **One-file-per-policy** means policies are individually trackable in git history — useful when investigating regressions ("when did this policy fail starting?").
