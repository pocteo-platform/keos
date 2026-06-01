---
name: Feature request
about: Something Keos doesn't do today but you want it to.
title: "feat: "
labels: ["enhancement", "triage"]
assignees: []
---

## What problem are you trying to solve?

<!-- Lead with the problem, not the solution. "I want X" is OK, but
"I have problem Y, X is one possible solution" gets you a better discussion. -->

## What would the ideal solution look like?

<!-- User-facing description. CLI command? UI affordance? New runbook
scenario? Helm value? Try to scope the smallest change that solves the
problem. -->

## Alternatives considered

<!-- What else could work? Why isn't it the right call? This saves a
round-trip in review. -->

## Non-goals

<!-- What's explicitly OUT of scope for this request? Helps reviewers
avoid scope creep. -->

## How does this fit Keos's architecture?

<!-- For non-trivial changes, link the relevant section of
`docs/ARCHITECTURE.md`. If your change requires a new external service,
a new persisted state shape, or a new action in the catalog, this issue
becomes an ADR — see CONTRIBUTING.md §"Architecture decisions". -->

## Affected areas

- [ ] Agent graph (`agent/graph.py`)
- [ ] LLM prompts (`agent/llm.py`)
- [ ] Action catalog (`agent/tools.py`, `mcp_server/`, `policies/agent.rego`)
- [ ] Observer / event bus (`agent/chaos_observer.py`, `agent/serve.py`)
- [ ] Web UI (`frontend/`)
- [ ] Helm chart (`deploy/helm/pocteo/`)
- [ ] Documentation only
- [ ] Other (specify)

## Operator impact

<!-- New env var? New Helm value? Default chosen so existing installs
remain unaffected? Backward-compat constraints? -->
