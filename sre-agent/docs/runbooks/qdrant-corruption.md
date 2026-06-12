# Runbook: Qdrant corruption / RAG empty

## Symptom

- Incidents detail page: **Runbook context** tab shows "No runbook hits
  recorded" for every recent incident.
- Log lines: `vector_search exhausted retries; returning empty hits`.
- The agent escalates more than usual (lower-confidence diagnoses
  without runbook context tend to fall below the 0.6 threshold).
- `curl http://qdrant:6333/collections/runbooks` returns 404 or 500.
- `curl http://qdrant:6333/healthz` returns 5xx.

This is a **graceful-degrade** failure mode by design — the graph
continues, just blind. The agent is more conservative without RAG.

## Triage (5 min)

```bash
# 1. Pod state.
kubectl -n pocteo get pod qdrant-0
kubectl -n pocteo logs qdrant-0 --tail=80

# 2. Collection state.
kubectl -n pocteo exec deploy/api -- \
  curl -sS http://qdrant:6333/collections/runbooks

# 3. PVC + disk.
kubectl -n pocteo get pvc data-qdrant-0
kubectl -n pocteo exec qdrant-0 -- df -h /qdrant/storage

# 4. Doctor reports the RAG store state.
kubectl -n pocteo exec deploy/api -- python -m scripts.doctor
```

| Pattern | Cause | Jump to |
|---|---|---|
| `collection runbooks not found` | Wiped or never seeded | Resolution §A |
| `points_count: 0` despite collection existing | Same as above (re-seed path identical) | Resolution §A |
| Embedding dim mismatch error | Model changed without re-seeding | Resolution §B |
| Pod CrashLoopBackOff / EOF reads | Storage corrupted | Resolution §C |

## Containment

No containment needed — `vector_search` is already returning `[]`
gracefully. The agent's diagnose step works without RAG, just with lower
confidence. If escalations are high enough to be operationally annoying,
flip mode to `observe-only` so the (slightly worse) diagnoses never
reach execute:

```bash
kubectl patch agentmode default --type=merge \
  -p '{"spec":{"mode":"observe-only","note":"runbook: qdrant-corruption"}}'
```

## Resolution

### §A Re-seed (the common case)

`agent/seed_rag.py` reads `agent/runbooks/*.md` and embeds each one into
Qdrant. **It's idempotent and re-runnable.** This is the fix 90% of the time.

```bash
kubectl -n pocteo exec deploy/api -- python -m agent.seed_rag

# Expected output:
#   INFO seed_rag :: embedding 001-memory-leak (2320 chars)
#   ...
#   INFO seed_rag :: upserted 7 runbooks into 'runbooks'
#   INFO seed_rag :: collection now holds 7 points

# Verify.
kubectl -n pocteo exec deploy/api -- python -m scripts.doctor
# Look for: [OK]  QDRANT       points=7
```

### §B Dimension mismatch (you changed `OLLAMA_EMBED_MODEL`)

If someone switched `ollama.embedModel` (e.g. nomic-embed-text →
mxbai-embed-large), the new embeddings have a different dimension than
the existing collection. Drop and re-create:

```bash
# Drop the collection.
kubectl -n pocteo exec deploy/api -- \
  curl -sS -X DELETE http://qdrant:6333/collections/runbooks

# Re-seed (seed_rag creates with the dim of the currently-configured model).
kubectl -n pocteo exec deploy/api -- python -m agent.seed_rag
```

### §C Storage corruption

```bash
# Confirm Qdrant can't recover on its own.
kubectl -n pocteo delete pod qdrant-0
kubectl -n pocteo wait --for=condition=ready pod/qdrant-0 --timeout=60s
kubectl -n pocteo logs qdrant-0 --tail=20
# Still failing → nuke the PVC and start fresh.

# Nuke + restore from runbook source files (NOT a backup snapshot).
kubectl -n pocteo scale sts qdrant --replicas=0
kubectl -n pocteo delete pvc data-qdrant-0
kubectl -n pocteo scale sts qdrant --replicas=1
kubectl -n pocteo wait --for=condition=ready pod/qdrant-0 --timeout=60s

kubectl -n pocteo exec deploy/api -- python -m agent.seed_rag
```

**Why no backup restore?** The 7 runbook source files in
`agent/runbooks/*.md` are checked into Git. Re-seeding from them is
deterministic + always reproducible + faster than restoring a PVC
snapshot. The only thing a Qdrant backup would buy is preserved
operator-authored runbooks attached to use cases — those are also
re-derived on the next use-case Run (see `agent/usecase_runner.py
::_seed_runbook_if_present`).

## After the fix

```bash
# Bring mode back to active.
kubectl patch agentmode default --type=merge \
  -p '{"spec":{"mode":"active","note":"runbook: qdrant-corruption cleared"}}'

# Trigger a use case; verify the Runbook context tab is populated.
TOKEN=$(kubectl -n pocteo get secret pocteo-pocteo-auth -o jsonpath='{.data.SHP_API_TOKEN}' | base64 -d)
curl -sS -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost/api/usecases/lib-memory-leak/run
# In the SPA, open /incidents/<id> → Runbook context tab should show ≥1 hit.
```

## Postmortem template

Same shape as [agent-api-down.md](agent-api-down.md#postmortem-template).
For the "what we'll change" section:

- If this happened because someone changed `ollama.embedModel` without
  re-seeding, the upgrading runbook + CONFIG_REFERENCE.md should call
  it out more loudly.
- If storage corruption was hardware, consider a replicated CSI
  (longhorn, openebs jiva) for the Qdrant PVC.
