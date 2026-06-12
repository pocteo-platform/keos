# Contributing to Keos SRE Agent

Thanks for being interested. This doc covers everything you need to land a
change cleanly: how to set up a dev environment, what tests are required,
how commits should look, and what the review process expects.

## Dev environment

Pick the host you'll actually develop on:

- **Windows + PowerShell:** see [docs/INSTALLATION.md §1](docs/INSTALLATION.md). You'll
  drive everything through `.\make.ps1 <target>`.
- **macOS / Linux:** same doc; substitute `make <target>`.

One-time setup:

```bash
make install     # installs uv, k3d, opa, pulls models, brings up compose
                 # + k3d cluster + seeds Qdrant. ~10 min on cold machine.
make doctor      # confirms every dependency is wired correctly.
```

If `doctor` reports anything red, fix THAT before opening a PR.

## Day-to-day

| Need | Command |
|---|---|
| Run the full hermetic test suite | `make test` |
| Run policy tests against live OPA | `make test-policy` |
| Lint Python + frontend | `make lint` |
| Auto-format both | `make format` |
| Type-check (mypy + tsc) | `make typecheck` |
| Coverage report | `make coverage` |
| Rebuild api/mcp-server/frontend after a code change | `make rebuild` |
| Tail logs | `make logs` |

The full demo loop:

```bash
make demo-ui     # opens http://localhost in your browser
```

## Required checks before opening a PR

Run locally — these are also enforced in CI but you'll get faster feedback
from `make`:

```bash
make lint        # ruff + eslint must be clean
make typecheck   # mypy + tsc must be clean (mypy is advisory in CI; tsc is gating)
make test        # all hermetic tests pass
helm lint deploy/helm/pocteo --set-string auth.apiToken=test --set-string auth.hmacKey=test
```

If you touched the Helm chart, also render against the strict configs CI uses:

```bash
helm template pocteo deploy/helm/pocteo \
  --set-string auth.apiToken=t --set-string auth.hmacKey=t \
  --set monitoring.enabled=true --set networkPolicy.enabled=true \
  | kubeconform -strict -summary -ignore-missing-schemas \
                -schema-location default
```

## Commit + branch conventions

- One logical change per commit. If a PR has 5 commits, each should make
  sense on its own (CI re-runs across them).
- **Conventional Commits** for the subject line. The categories we use:

  ```
  feat:     new user-facing behaviour
  fix:      bug fix
  docs:     documentation only
  test:     test code only
  refactor: code change with no behaviour change
  perf:     performance improvement
  chore:    build / tooling / deps
  ci:       CI workflow change
  ```

  Example: `fix(graph): route stale events to escalate without paging`.

- Branch naming: `<your-handle>/<short-slug>` — e.g.
  `ahmed/fix-stale-event-skip-page-human`.

- **DCO sign-off required**. Every commit must include
  `Signed-off-by: Your Name <your@email>`. Use `git commit -s` to add it
  automatically. This is enforced by the DCO check in CI.

## Code style

Python:

- Formatted with `ruff format` (100-col, double quotes — see `pyproject.toml`
  `[tool.ruff]`). `make format` applies it.
- Lint rules: E, F, I, B, UP. See `pyproject.toml` `[tool.ruff.lint]` for
  the rationale on each ignore.
- Type hints encouraged but not enforced (mypy advisory in v1).

Frontend:

- TypeScript strict mode is on (`tsconfig.app.json`). No `any` warnings
  block CI today; please don't add new ones.
- ESLint flat config in `frontend/eslint.config.js`. `react-hooks/*` rules
  are HARD failures — they catch real bugs.
- Run `cd frontend && npm run lint` before pushing.

## PR review process

1. **Open against `main`.** A reviewer is auto-assigned from CODEOWNERS.
2. **CI must be green.** Required checks: `python-lint`, `python-test`,
   `frontend-lint`, `frontend-typecheck`, `frontend-build`, `helm-lint`,
   `helm-template-validate`, `opa-test`, `docker-build`.
3. **At least one approval** from a code-owner of the touched paths.
4. **Squash + merge** is the default. The squash commit message uses the
   PR title; keep it Conventional-Commits-shaped.
5. If the PR is breaking, the description MUST include a `BREAKING CHANGE:`
   footer — that triggers a major version bump on the next release.

## What needs a release-notes mention

Update `CHANGELOG.md`'s `## [Unreleased]` section in the same PR for:

- Anything visible to operators or end users (new endpoint, new Helm value,
  new UI flow, new env var).
- Anything that changes default behavior (new default value, new probe,
  new alert).
- Security fixes (cross-reference the CVE / advisory in `SECURITY.md`).

You can skip the changelog for: pure refactors, test additions, lint
fixes, dependency bumps from Dependabot.

## Architecture decisions

For non-trivial design choices (new external dep, new persisted state,
breaking change to the action catalog), open a discussion issue FIRST
with an ADR-shaped writeup. Template:

```
## Context
What problem are we solving? What constraints exist?

## Options considered
- Option A — pros, cons
- Option B — pros, cons
- Option C — pros, cons

## Decision
Which option and why. What gets ruled out.

## Consequences
What changes downstream. What gets harder. What gets easier.
```

Once consensus lands in the issue, open the PR referencing it.

## Reporting a vulnerability

DO NOT open a public issue for security bugs. See [SECURITY.md](SECURITY.md)
for the private disclosure flow.

## Code of Conduct

Be kind. Disagree on technical merit, not identity. Reviews are about the
code, not the author. If something feels off, message a maintainer.
