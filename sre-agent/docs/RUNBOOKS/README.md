# Operational runbooks

The agent watches your cluster. These docs help you when the agent
itself misbehaves.

## Decision tree

Start at the symptom you observed:

```
                        Is the agent producing incidents?
                                   /          \
                                 No            Yes
                                 |             |
                       ┌─────────┴────────┐    │
                       │                  │    │
                  /api/health           Pods    │
                  returns 5xx          stuck    │
                  or doesn't reply    Pending   │
                       │                 │      │
                       ▼                 ▼      │
            agent-api-down.md   (Is it a real   │
                                 cluster        │
                                 issue or a     │
                                 cordon? See    │
                                 USE_CASES.md)  │
                                                │
                  ┌─────────────────────────────┘
                  │
        Are they correct + completing?
              /            \
           Yes              No
            │                │
            ▼                ▼
        (you're            What's wrong?
         fine)               │
                  ┌──────────┼──────────┬──────────────┐
                  │          │          │              │
                Stuck      Same      LLM is         Audit
                 in        event     timing out     records
              CrashLoop    looping   / hanging      verify
              or no        / not                    failing
              progress     completing
                  │          │          │              │
                  ▼          ▼          ▼              ▼
            nats-down  runaway-      ollama-       audit-
             .md         graph.md     down.md      tampering.md
```

Plus three runbooks for explicit operations (not failure modes):

- **[upgrading.md](upgrading.md)** — Helm upgrade flow, pre-upgrade
  checks, rollback procedure.
- **[disaster-recovery.md](disaster-recovery.md)** — Full restore from
  backup. References [BACKUP_RESTORE.md](../BACKUP_RESTORE.md).
- **[secret-rotation.md](secret-rotation.md)** — Rotate
  `SHP_API_TOKEN`, `AUDIT_HMAC_KEY`, Slack webhook, Alertmanager token.

And one cross-cutting runbook for the worst-case scenario:

- **[incident-response-agent.md](incident-response-agent.md)** — When
  THE AGENT is the incident. Kill-switch flow, full stack pause, post-mortem.

## Runbook shape

Every runbook in this directory uses the same 5 sections:

1. **Symptom.** What you observed — error message, stuck state, paged alert.
2. **Triage (5 min).** Cheap checks to confirm you're in the right runbook
   and narrow the cause. Never destructive.
3. **Containment.** Stop the bleeding. Flip the mode to `frozen`, drain a
   node, scale down — whatever buys time without making things worse.
4. **Resolution.** Actually fix it. Step-by-step commands with expected
   output at each step.
5. **Postmortem template.** A 5-section template you can paste into your
   incident retrospective so the next on-call doesn't re-discover the cause.

If you find yourself improvising heavily through a runbook, that's a
signal to file a PR — the runbook should match reality.

## Index

| File | When to read | Time-to-recovery |
|---|---|---|
| [agent-api-down.md](agent-api-down.md) | `/api/health` returns 5xx, doesn't reply, or k8s probes are failing | ~5 min |
| [ollama-down.md](ollama-down.md) | LLM calls 503 / time out; `keos_escalations_total{reason="other"}` rising | ~10 min |
| [nats-down.md](nats-down.md) | No new incidents despite real cluster failures; consumer pending growing | ~5 min |
| [qdrant-corruption.md](qdrant-corruption.md) | RAG hits empty; `keos_chaos_observer_errors_total` clean but agent escalates with `not_healed` repeatedly | ~5 min |
| [audit-tampering.md](audit-tampering.md) | `verify_audit_log()` reports non-zero `indices_of_invalid` | ~30 min (forensic) |
| [runaway-graph.md](runaway-graph.md) | Same incident escalates 5+ times in 30 min | ~10 min |
| [drift-storm.md](drift-storm.md) | Phase 15 detector firing 100+ events in 5 min; Reliability page's GitOps drift tile in the dozens | ~10 min |
| [upgrading.md](upgrading.md) | About to `helm upgrade` to a new version | ~15 min (incl. drill) |
| [disaster-recovery.md](disaster-recovery.md) | The cluster / PVCs are gone, restore from backup | ~30 min |
| [incident-response-agent.md](incident-response-agent.md) | The agent itself took a destructive action you didn't expect | ~5 min (containment); hours (postmortem) |
| [secret-rotation.md](secret-rotation.md) | Token / key leaked, or scheduled rotation | ~10 min |

## See also

- [USER_GUIDE.md §10 Troubleshooting](../USER_GUIDE.md) — symptom catalog
  for non-emergency issues
- [OBSERVABILITY.md](../OBSERVABILITY.md) — alert rules that fire these
  runbooks
- [BACKUP_RESTORE.md](../BACKUP_RESTORE.md) — what's recoverable from
  backups, and how
