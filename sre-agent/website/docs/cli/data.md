---
id: data
slug: /cli/data
title: Data plane
sidebar_position: 5
description: keosctl seed · models — manage the runbook corpus and the Ollama LLM models on the Keos node.
---

# Data plane

Two command groups govern what the agent's local data plane contains: `seed` (runbook corpus in Qdrant) and `models` (LLM weights in Ollama). Both reach into the running pods via `kubectl exec`.

## `keosctl seed` — runbook corpus

Re-seeds the Qdrant `runbooks` collection from `sre-agent/agent/runbooks/*.md`. Called automatically by `keosctl install`, but you'll re-run it whenever:

- You add or edit a runbook in `agent/runbooks/`
- `keosctl doctor` reports `runbook corpus: no points found`
- You bump the embedding model and want to re-embed the whole corpus

```bash
$ keosctl seed

Seeding runbooks into Qdrant…
{"taskName": null, "ts": "2026-06-09T08:30:00", "level": "INFO", "logger": "seed_rag", "msg": "embedding 001-memory-leak (1842 chars)"}
{"taskName": null, "ts": "2026-06-09T08:30:05", "level": "INFO", "logger": "seed_rag", "msg": "embedding 002-crashloop (1654 chars)"}
…
{"taskName": null, "ts": "2026-06-09T08:30:58", "level": "INFO", "logger": "seed_rag", "msg": "upserted 12 runbooks into 'runbooks'"}
{"taskName": null, "ts": "2026-06-09T08:30:58", "level": "INFO", "logger": "seed_rag", "msg": "collection now holds 12 points"}
  ✓ Done
```

Under the hood:
1. `kubectl exec api -- python -m agent.seed_rag`
2. The Python module reads every `.md` file in `agent/runbooks/`
3. For each file, calls Ollama `/api/embeddings` with the embed model (`nomic-embed-text` by default)
4. Upserts each runbook as a Qdrant point with the embedding + metadata

If the embed model is missing, the seed fails with HTTP 404. Fix:

```bash
keosctl models pull nomic-embed-text
keosctl seed
```

## `keosctl models` — LLM management

Wraps the `ollama` CLI inside the Ollama pod with consistent flags.

### `models list`

```bash
$ keosctl models list
NAME                          ID              SIZE      MODIFIED
qwen2.5:3b-instruct-q4_K_M    357c53fb659c    1.9 GB    18 minutes ago
nomic-embed-text:latest       0a109f422b47    274 MB    19 minutes ago
```

### `models pull <model>`

Pulls a new model into Ollama. The model becomes available immediately for the agent's next graph run.

```bash
keosctl models pull qwen2.5:7b-instruct-q4_K_M    # ~4.5 GB, ~15 s/call on Jetson
keosctl models pull llama3.2:3b-instruct-q4_K_M   # alternative chat model
keosctl models pull nomic-embed-text              # embed model (needed for seed)
```

This call **blocks until the pull completes**. On a cold start with a 4 GB model + slow uplink, that's 10-15 min. For automation, run with `&` or in a separate terminal.

### Switching the chat model

The chat model is read from the agent's env var `OLLAMA_CHAT_MODEL`. To change it permanently:

```bash
# 1. Pull the new model
keosctl models pull qwen2.5:7b-instruct-q4_K_M

# 2. Patch the Helm release
helm upgrade keos sre-agent/deploy/helm/pocteo -n keos \
  --reuse-values \
  --set ollama.chatModel=qwen2.5:7b-instruct-q4_K_M

# 3. Restart the API pod to pick up the new env var
keosctl restart api
```

For one-shot testing, set the env var directly:

```bash
kubectl set env deployment/api -n keos OLLAMA_CHAT_MODEL=qwen2.5:7b-instruct-q4_K_M
```

### `models rm <model>`

Free up disk:

```bash
keosctl models rm qwen2.5:1.5b-instruct-q4_K_M     # the small fallback
```

Don't remove the model the agent is actively using, or graph runs will hard-fail at the diagnose node.

### `models show <model>`

Print the Ollama model card (system prompt, template, parameters):

```bash
$ keosctl models show qwen2.5:3b-instruct-q4_K_M
  Model: qwen2.5:3b-instruct-q4_K_M
  System: You are an autonomous SRE agent…
  Parameters:
    num_ctx 4096
    temperature 0.7
  …
```

## Disk budget

| Model | Size on disk | RAM at load | Typical use |
|---|---|---|---|
| `nomic-embed-text` | 274 MB | ~600 MB | Mandatory (for seed + RAG retrieval) |
| `qwen2.5:1.5b-instruct-q4_K_M` | 990 MB | ~1.5 GB | Smallest. Lower quality, fastest. |
| **`qwen2.5:3b-instruct-q4_K_M`** | **1.9 GB** | **~2.5 GB** | **Default. Jetson Orin sweet spot.** |
| `qwen2.5:7b-instruct-q4_K_M` | 4.5 GB | ~5.5 GB | Highest quality. Needs ≥ 8 GB RAM free. |
| `llama3.2:3b-instruct-q4_K_M` | 2.0 GB | ~2.5 GB | Alternative to Qwen. Similar quality. |

The Ollama PVC ships at 10 GiB by default. Bump if you want to hold multiple variants:

```bash
helm upgrade keos sre-agent/deploy/helm/pocteo -n keos \
  --reuse-values --set ollama.persistence.size=30Gi
```

## Common workflows

### "I edited a runbook; how do I make the agent see it?"

```bash
vi sre-agent/agent/runbooks/001-memory-leak.md
keosctl seed                       # re-embeds every runbook
```

### "I want to evaluate the 7B model side-by-side"

```bash
keosctl models pull qwen2.5:7b-instruct-q4_K_M
# Trigger a use case while keeping the 3B as default:
keosctl run lib-memory-leak
# Note the confidence + outcome, then switch the env var (above) and run again
keosctl run lib-memory-leak
keosctl events                     # compare side by side
```

### "Air-gapped — how do I get a model in without internet?"

```bash
# On the build host (online):
docker run --rm -v $(pwd)/models:/root/.ollama ollama/ollama \
  ollama pull qwen2.5:3b-instruct-q4_K_M

# Then sync the ./models dir to the air-gapped node's Ollama PVC:
kubectl -n keos cp ./models/manifests ollama-0:/root/.ollama/manifests
kubectl -n keos cp ./models/models    ollama-0:/root/.ollama/models
keosctl models list                  # confirm it shows up
```

For the full air-gap install flow, see [Air-gapped install](../platforms/airgap.md).

## See also

- [Agent commands](./agent) — `events`, `run`, `usecases`
- [ML baselines architecture](../architecture/ml-baselines.md) — how the agent uses embeddings
