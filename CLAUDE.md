# Project notes for Claude

Operational context for any Claude Code session in this repo. The README is for users; AGENTS.md is for cross-agent guidance about *using* the MCP. **This file is for Claude when *working on the project itself*** — branch protection, release flow, naming gotchas.

## Repo & PyPI

- **Repo:** https://github.com/AaronGoldsmith/mcp-printable (public)
- **PyPI:** https://pypi.org/project/mcp-printable/
- **Default branch:** `main` (protected — see below)
- **Maintainer:** AaronGoldsmith (solo)

## Branch protection on `main` is ON

- **No direct push to `main`.** All changes go through pull requests.
- **Squash-merge only.** Repo-level setting: merge commits and rebase merges are disabled.
- **Linear history required.**
- **Force pushes blocked, branch deletion blocked.**
- **Required approvals: 0** — solo-friendly. PRs can land without external review, but they must *exist*.
- **Conversation resolution required** before merge.
- **`enforce_admins: false`** — the maintainer can bypass via the GitHub UI in emergencies. Don't assume CLI bypass works.

### Normal change flow

```bash
git switch -c <type>/<short-description>     # e.g. fix/scad-kwarg, docs/readme-tweak
# ... edit + test ...
git add <specific-files>
git commit -m "..."
git push -u origin HEAD
gh pr create --title "..." --body "..."
gh pr merge --squash --delete-branch          # or click squash button in UI
```

If you forget and `git push origin main`, GitHub will reject. Don't try to bypass — open a branch + PR.

## Release pipeline

Tag-driven. The pipeline is set up via `.github/workflows/publish-to-pypi.yml`:

- **Push to `main`** → build job runs (validates the wheel builds). No publish.
- **Manual `workflow_dispatch`** → build + TestPyPI publish (sanity check before tagging).
- **Push tag `vX.Y.Z`** → build + production PyPI publish via OIDC trusted publishing. Uses GitHub Environment `pypi`.

Cutting a release:

1. On a branch, bump `version = "X.Y.Z"` in `pyproject.toml`.
2. PR → squash-merge to `main`.
3. From `main`: tag with annotated message via heredoc (NOT inline `-m "...`backticks`..."` — bash interprets backticks in double-quoted strings as command substitution and eats the names):
   ```bash
   git tag -a vX.Y.Z -F - <<'EOF'
   vX.Y.Z

   Short summary.
   - Bullet
   - Bullet
   EOF
   git push origin vX.Y.Z
   ```
4. GHA publishes to PyPI within ~1 min. Watch via `gh run list --repo AaronGoldsmith/mcp-printable --limit 3`.

### Yanked versions

- `0.1.0`, `0.1.1` — broken MCP resources (`docs/` wasn't in the wheel; fixed in 0.1.2). Already yanked on PyPI.

## Naming conventions (four-name story)

See [`docs/internals/naming.md`](docs/internals/naming.md) for the full breakdown. Quick reference:

| Name | What |
|---|---|
| `mcp-printable` | PyPI package name |
| `printable` | CLI command name (the entry script) |
| `printable_blender` (underscore) | FastMCP server name + Blender addon module name. **Legacy / internal — don't rename without a migration plan; would break installed addons.** |
| `printable` (recommended) | mcpServers entry key in user `.mcp.json` — freeform but we standardized on this |

If you find yourself wondering "why is this called `printable_blender` and not `printable`?" — see the comment near `FastMCP("printable_blender", ...)` in [`server.py`](server.py).

## Tests

- **Unit:** `python -m pytest tests/` — 32+ tests, no Blender required, ~3s.
- **Eval self-test:** `python -m evals.runner --self-test` — procedural judges on canned good/bad traces, no LLM cost.
- **Live eval (costs API credits + needs Blender open):**
  ```bash
  python -m evals.runner --scenario simple-cube --agent goose \
    --provider openrouter --model "qwen/qwen3.6-plus" \
    --log-dir evals/results/logs
  ```

## Bundled docs / MCP resources

The `docs/` directory IS bundled in the wheel — `pyproject.toml`'s `[tool.hatch.build.targets.wheel] only-include = [..., "docs"]`. Each markdown under `docs/` that's exposed as an MCP resource also has a `@mcp.resource("printable://...")` decorator in [`server.py`](server.py) backed by `_read_doc("relative/path.md")`. Adding a new resource means: write the markdown, add the decorator, add the row in README's Documents table.

## Local dev / testing the published wheel

The local `.mcp.json` (gitignored) uses `uvx --refresh --from mcp-printable printable` so each session pulls the latest published version from PyPI. To pin during dev: `uvx --from mcp-printable@<version> printable`. To use the in-repo source: change command to `uv run --directory <repo-path> printable`.

## Current roadmap (loosely tracked here, see README "Roadmap" section for the user-facing version)

- `blender_launch` / `blender_status` / `blender_kill` tools — let the agent start Blender itself instead of relying on user
- `printable://setup` resource — agent fetches setup instructions on connection error
- Better `BlenderConnection` error messages when addon isn't responding
- OpenSCAD parity validators (clearance sweep, retention, thin walls)
- Validated parameter recipes (wheel-on-axle, flip-tile, ball-and-socket, snap-fit) — pulled from earlier draft, awaiting print validation

## What NOT to do without explicit confirmation

- Push directly to `main` (the protection will reject anyway, but don't try)
- Bump version + tag in the same step as a feature commit (separate the version bump PR; tag from `main` after merge)
- Rename `printable_blender` (the internal name) — breaks installed addons
- Re-publish a yanked version
- Add `--check-url` back to the production publish step in the workflow (intentionally absent so re-pushed tags fail loudly)
