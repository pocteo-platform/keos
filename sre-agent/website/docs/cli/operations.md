---
id: operations
slug: /cli/operations
title: Day-2 operations
sidebar_position: 3
description: keosctl status · doctor · mode · logs · ui · token · restart · shell · describe · port-forward — the everyday operator surface.
---

# Day-2 operations

These are the commands you'll run dozens of times per week. All of them honour the global `-n`, `-r`, `--kubeconfig`, `--no-color` flags.

## `keosctl status` — release + pods + mode

The single-pane summary. Default mode renders a coloured table:

```bash
$ keosctl status

Keos status
────────────────────────────────────────────────────────────────
  › Release       pocteo-1.0.1  (deployed)
  › Namespace     keos
  › Agent mode    observe-only

Pods
  ✓ api-c76966dd-sbk9b              1/1        37m
  ✓ caddy-7899795d7-4swk6           1/1        37m
  ✓ chaos-controller-manager-…      1/1        26m
  ✓ chaos-daemon-c9f6x              1/1        26m
  ✓ frontend-7ffbb6dccd-vr8c9       1/1        37m
  ✓ mcp-server-68d7877b8c-2svc8     1/1        37m
  ✓ nats-0                          1/1        37m
  ✓ ollama-0                        1/1        37m
  ✓ opa-5497f44f56-6ngc6            1/1        37m
  ✓ qdrant-0                        1/1        37m
  13/13 pods Ready

Access
  Open the SPA:   keosctl ui
  Get the token:  keosctl token
  Tail the agent: keosctl logs api -f
```

### Flags

- `--watch`, `-w` — refresh every 2 s (`--interval N` to change the cadence). Ctrl-C to stop.
- `--json` — machine-readable JSON for monitoring scripts:

```bash
$ keosctl status --json
{
  "namespace": "keos",
  "release": "keos",
  "mode": "observe-only",
  "pods": [
    { "name": "api-c76966dd-sbk9b", "phase": "Running", "ready": true },
    …
  ],
  "pods_ready": 13,
  "pods_total": 13
}
```

## `keosctl doctor` — health check

Runs a full system check across every component. Catches what `status` doesn't:

```bash
$ keosctl doctor

Keos doctor
────────────────────────────────────────────────────────────────
  ✓ Helm release deployed
  ✓ api              Running (ready)
  ✓ mcp-server       Running (ready)
  ✓ frontend         Running (ready)
  ✓ ollama           Running (ready)
  ✓ qdrant           Running (ready)
  ✓ nats             Running (ready)
  ✓ opa              Running (ready)
  ✓ caddy            Running (ready)
  ✓ AgentMode CR present (mode=observe-only)
  ✓ ollama models    2 present
  ✓ runbook corpus   12 points in Qdrant

  All checks passed.
```

What it actually probes:
- Helm release status (`deployed` vs `failed` vs missing)
- Per-component pod phase + readiness condition
- AgentMode CR present + mode value
- Ollama model count (≥ 2 means chat + embed are loaded)
- Qdrant `runbooks` collection point count (a value of 0 means seed didn't run)

Common findings + fix hints:

| Doctor says | Fix |
|---|---|
| `runbook corpus: no points found` | `keosctl seed` |
| `ollama models: only 1 loaded` | `keosctl models pull qwen2.5:3b-instruct-q4_K_M` |
| `mcp-server: not Ready` | `keosctl logs mcp-server --tail 50` then `keosctl restart mcp-server` |
| `AgentMode CR missing` | Re-apply the CR: `kubectl apply -f sre-agent/manifests/agentmode-default.yaml` |

Exit code is 0 on all-green, 1 on any finding — wire it into your monitoring or CI smoke-test.

## `keosctl mode` — flip the agent's autonomy

Without args, prints the current mode:

```bash
$ keosctl mode
observe-only
```

With an arg, flips the mode. `active` requires explicit confirmation (the agent is about to mutate the cluster):

```bash
$ keosctl mode active
  ⚠ Switching to active — agent will mutate the cluster.
  Continue? [y/N] y
  ✓ Agent mode set to active

$ keosctl mode observe-only
  ✓ Agent mode set to observe-only

$ keosctl mode frozen        # maintenance window: no graph runs
  ✓ Agent mode set to frozen
```

What each mode does:

| Mode | Graph runs? | Mutates the cluster? | Use case |
|---|---|---|---|
| `observe-only` | yes | **no** (dry-run) | First install, evaluation, audit-only |
| `active` | yes | **yes** (after OPA gate) | Production use |
| `frozen` | **no** | no | Maintenance windows, suspected agent misbehaviour |

The mode is hot-flippable. Switching to `frozen` mid-incident **stops the running graph at the next state transition**.

## `keosctl logs <component>` — tail pod logs

Wraps `kubectl logs -l app.kubernetes.io/component=<X>`:

```bash
keosctl logs api                  # last 100 lines, then exit
keosctl logs api -f               # follow (Ctrl-C to stop)
keosctl logs api --tail 1000      # bigger backlog
keosctl logs api --previous       # the previous container instance (post-crash)
```

Supported components: `api`, `mcp-server`, `frontend`, `caddy`, `ollama`, `qdrant`, `nats`, `opa`.

Run `keosctl logs` with no args to print the component list.

## `keosctl ui` — port-forward + open the browser

The all-in-one "let me see the SPA" command:

```bash
$ keosctl ui

Opening Keos SPA
────────────────────────────────────────────────────────────────
  › URL           http://localhost:8080
  › Token         1XLAnazIX17XU+6NapXiTsX+CmBPs379
    (paste this into the login screen; also cached at ~/.keos-token)

  Port-forwarding svc/pocteo on :8080 — press Ctrl-C to stop.
```

What it does:
1. Forwards `svc/pocteo` to `localhost:8080`
2. Prints the URL + the cached bearer token
3. Tries to open your default browser via `xdg-open` (Linux) / `open` (macOS) / `powershell.exe Start-Process` (WSL → Windows)

Flags:
- `--port N` — use a different local port (default 8080)
- `--no-open` — don't auto-open the browser (useful when SSH-tunneling)

For programmatic use, see [`port-forward`](#keosctl-port-forward).

## `keosctl token` — print the bearer

```bash
$ keosctl token
1XLAnazIX17XU+6NapXiTsX+CmBPs379
```

On first call, reads it from the K8s Secret + caches at `~/.keos-token` (`umask 0077`). Subsequent calls are instant.

For scripts:

```bash
TOKEN=$(keosctl token)
curl -H "Authorization: Bearer $TOKEN" http://localhost:8080/api/health
```

## `keosctl restart <component>` — rolling restart

Restarts one component cleanly:

```bash
keosctl restart api               # Deployment → kubectl rollout restart
keosctl restart ollama            # StatefulSet → kubectl delete pod (recreated)
```

Behaviour depends on the workload kind: Deployments get a rollout, StatefulSets (Ollama, Qdrant, NATS) get pod-delete (the StatefulSet then respawns the replica). Supported components: same set as `logs`.

## `keosctl shell <component>` — interactive exec

```bash
keosctl shell api                 # tries bash, falls back to sh
keosctl shell ollama              # for `ollama ls` etc. directly inside the pod
```

Equivalent to `kubectl exec -it <pod> -- bash`, but resolves the right pod name for you and falls back to `sh` if `bash` isn't in the image.

## `keosctl describe <component>` — kubectl describe shortcut

```bash
keosctl describe api              # full kubectl describe of the first api pod
```

Useful for digging into events, init-container state, and resource allocations during troubleshooting.

## `keosctl port-forward <component> <port>` — generic forward

The non-UI port-forward (the `ui` command is a specialised wrapper around this). Knows the correct `targetPort` for every component:

```bash
keosctl port-forward api 8000     # local 8000 → api Service :8000
keosctl port-forward ollama 11434 # talk to Ollama directly from your laptop
keosctl port-forward qdrant 6333  # talk to Qdrant for debugging
keosctl port-forward pocteo 9090  # the Caddy ingress, alternative to `ui`
```

Aliases for short typing:

```bash
keosctl pf api 8000               # same as port-forward
```

## Common workflows

### "I want to know why an incident escalated"

```bash
keosctl events -n 10              # find the incident id
keosctl logs api --tail 200 -f    # tail what the agent's saying right now
```

Or use the SPA's Pipeline tab via `keosctl ui` — the incident replay there is richer than raw logs.

### "Pods aren't coming back from a node reboot"

```bash
keosctl status
keosctl doctor                    # narrows down which component is stuck
keosctl describe <bad-component>  # full kubectl describe for the failing pod
keosctl restart <bad-component>   # clean rollout
```

### "I want to monitor the agent from a dashboard"

```bash
# Wire `keosctl status --json` into a cron + your monitoring stack.
*/2 * * * * keosctl status --json --no-color > /var/lib/monitor/keos.json
```

Or expose Prometheus metrics directly:

```bash
keosctl port-forward api 8000
curl http://localhost:8000/metrics
```

## See also

- [Agent commands](./agent) — `events`, `run`, `usecases`, `audit`
- [Data commands](./data) — `seed`, `models`
- [Observability](../operations/observability.md) — Prometheus + Grafana setup
