---
id: ollama-down
slug: /runbooks/ollama-down
title: Ollama is down
sidebar_position: 3
---

# Runbook: Ollama down / LLM unavailable

## Symptom

- `keos_escalations_total{reason="other"}` is climbing.
- API logs show `OllamaUnavailable: ollama unreachable after 3 attempts`
  in repeated graph runs.
- `kubectl -n pocteo exec deploy/api -- curl -sS http://ollama:11434/api/tags`
  returns connection refused, 500, or hangs.
- Pipeline view: graph runs reach `diagnose` then immediately escalate
  with `escalation_reason: "ollama unreachable"`.

## Triage (5 min)

```bash
# 1. Pod state.
kubectl -n pocteo get pod ollama-0 -o wide
kubectl -n pocteo describe pod ollama-0 | tail -30

# 2. Recent logs.
kubectl -n pocteo logs ollama-0 --tail=80

# 3. Models loaded?
kubectl -n pocteo exec ollama-0 -- ollama list

# 4. Disk on the model PVC.
kubectl -n pocteo exec ollama-0 -- df -h /root/.ollama

# 5. From the api side — what's the connection error?
kubectl -n pocteo exec deploy/api -- \
  curl -sS --max-time 5 http://ollama:11434/api/tags 2>&1 | head -5
```

| Pattern | Cause | Jump to |
|---|---|---|
| `0/1 Running, Pending` + `0/1 nodes are available: 1 Insufficient nvidia.com/gpu` | GPU disappeared (Jetson) | [JETSON.md](/docs/platforms/jetson) §Troubleshooting |
| `OOMKilled` in pod events | Resource limit too tight for the model | Resolution §A |
| `no such file or directory` for the model file | Model never pulled or PVC wiped | Resolution §B |
| Connection refused, pod Running, model listed | Internal Ollama deadlock | Resolution §C |
| Pod Running, model listed, but every request 500s | Bad model file / corrupted weights | Resolution §D |

## Containment

```bash
# Flip mode to observe-only — the graph still runs through diagnose,
# but if it ends up at execute, OPA denies. Cheaper than `frozen` if
# you want the LLM-down condition logged in audit for forensics.
kubectl patch agentmode default --type=merge \
  -p '{"spec":{"mode":"observe-only","note":"runbook: ollama-down"}}'
```

## Resolution

### §A OOMKilled — model too big for the limit

```bash
# Inspect current limits.
kubectl -n pocteo get sts ollama -o jsonpath='{.spec.template.spec.containers[0].resources}'

# Bump (and/or switch to a smaller model).
helm upgrade pocteo deploy/helm/pocteo --reuse-values \
  --set ollama.resources.limits.memory=6Gi

# OR, drop to qwen2.5:1.5b (smaller, lower diagnostic quality but fits in
# 3 GB):
helm upgrade pocteo deploy/helm/pocteo --reuse-values \
  --set ollama.chatModel=qwen2.5:1.5b-instruct-q4_K_M

# Re-pull the model after switching.
kubectl -n pocteo exec deploy/api -- python -m agent.pull_models
```

### §B Model missing from the PVC

```bash
# Confirm.
kubectl -n pocteo exec ollama-0 -- ollama list
# (Should show qwen2.5:3b-instruct-q4_K_M + nomic-embed-text)

# If empty:
kubectl -n pocteo exec deploy/api -- python -m agent.pull_models

# Watch the pull (1-5 min depending on network):
kubectl -n pocteo logs deploy/api -f | grep -i ollama
```

### §C Internal deadlock (rare but real)

Ollama sometimes hangs when a request times out client-side and the
server is still loading the model. Symptom: pod Running, model listed,
but every request hangs forever.

```bash
# Restart the pod. JetStream consumer ack_wait is 600s so any
# in-flight graph runs will get nak'd cleanly and re-deliver.
kubectl -n pocteo rollout restart sts/ollama
kubectl -n pocteo wait --for=condition=ready pod/ollama-0 --timeout=120s

# Warm the model so the first real call doesn't pay cold-load latency.
kubectl -n pocteo exec deploy/api -- \
  python -c "from agent.llm import warmup_chat_model; warmup_chat_model()"
```

### §D Corrupted weights

```bash
# Drop the model and re-pull.
kubectl -n pocteo exec ollama-0 -- ollama rm qwen2.5:3b-instruct-q4_K_M
kubectl -n pocteo exec deploy/api -- python -m agent.pull_models
```

## After the fix

```bash
# Bring mode back to active.
kubectl patch agentmode default --type=merge \
  -p '{"spec":{"mode":"active","note":"runbook: ollama-down cleared"}}'

# Verify a real graph run completes.
TOKEN=$(kubectl -n pocteo get secret pocteo-pocteo-auth \
  -o jsonpath='{.data.SHP_API_TOKEN}' | base64 -d)
curl -sS -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost/api/usecases/lib-memory-leak/run
# Watch /pipeline/<id> in the SPA — should complete in 4-5 min CPU / 25 s GPU.
```

## Postmortem template

Same shape as [agent-api-down.md](agent-api-down.md#postmortem-template);
substitute "Ollama" for "API" in the impact section and reference the
chosen `ollama.chatModel` if you switched.
