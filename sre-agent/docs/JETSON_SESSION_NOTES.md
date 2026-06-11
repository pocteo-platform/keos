# Session notes — WSL debugging → Jetson handoff (2026-06-11)

> **Purpose.** This is a hand-off digest from the session that prepared the
> repo for the Jetson. A fresh Claude Code session on the Jetson does NOT
> share that conversation's memory — this file carries the story so you have
> full context. Read this, then `website/docs/platforms/jetson-kickoff.md`.

## The goal
Prove Keos's ollama LLM runs on the **Jetson Orin GPU** (it only ran on CPU
under WSL2 — ~9 min/incident, painfully slow), and validate the **security
self-healing loop** end-to-end on real hardware: a `lib-reverse-shell`
incident → diagnose → OPA-gated `isolate_pod` → real deny-all NetworkPolicy +
quarantine label → signed audit. On the GPU this should take **seconds**.

## What was debugged on WSL (and fixed — all on `main`)
Everything below is already in the code you cloned. Listed so you don't
re-diagnose them.

1. **`keosctl ui` / `port-forward` crashed** with `exec: kc: not found`.
   `kc` is a shell function; `exec` only runs real binaries. Fixed by dropping
   `exec`. (`59d7efe`)

2. **`keosctl: command not found` after install.** The installer symlinked it
   into `~/.local/bin` but that wasn't on PATH — a leftover `~/.bash_profile`
   shadowed Ubuntu's `~/.profile`. Installer now wires PATH per login-shell
   (bash/zsh/fish), distro-aware. (`3eab404`)

3. **UI showed 0 nodes / 0 pods / "Loading…".** The biggest bug. The
   `api`/`mcp-server`/`stateful-stores` NetworkPolicies allowed apiserver
   egress as `0.0.0.0/0 except RFC1918` on 443/6443 — but the apiserver
   ALWAYS lives on a private IP (ClusterIP `10.43.0.1`, node `172.x`), so the
   agent was firewalled out of its own cluster. New
   `networkPolicy.apiServerCIDRs` (default the private ranges) explicitly
   allows it; `allowPublicEgress` keeps external-LLM egress. (`5418ac6`)
   **→ On the Jetson this default works out of the box; no action needed.**

4. **`isolate_pod` failed** (`label exit 1 / apply exit 1`). Root cause: the
   `workloads`/`nginx-demo` target didn't exist — the edge installer never
   seeded demo workloads. Installer now applies `manifests/namespaces.yaml` +
   `demo-workloads.yaml` by default (`--no-demo-workloads` to opt out).
   (`29d7262`) Also: `isolate_pod` now surfaces the real kubectl stderr
   instead of bare exit codes. (`cee7074`)

5. **Security events wrongly skipped as "already_ready".** The triage
   staleness check is for SRE/infra (a pod that healed itself), but it was
   applied to security events too — a reverse shell in a *healthy* pod was
   skipped. Now domain-aware: never `already_ready`-skip a security event.
   (`cee7074`)

6. **Ghost-incident loop.** The observer re-published `infra.events.unhealthy`
   for healthy pods on every recurring kubelet probe warning → endless
   auto-resolved incidents. Now it readiness-checks the pod before publishing.
   (`e8f231d`)

7. **Falco + Tetragon now default-on** (`--no-security` opts out) with a
   wizard prompt. On WSL2 they CRASH (kernel can't attach the eBPF probe —
   `scap_init` fails). **On the Jetson's real kernel they will run** — that's
   one of the things to confirm. (`0b187ef`)

## The Jetson-specific risk to watch
**JetPack ↔ CUDA ↔ ollama version mismatch.** Symptoms: ollama returns HTTP
500 on generate, or `ollama ps` says `100% CPU` (silent fallback). We hit the
discrete-GPU cousin of this on the dev box (a stale model blob → 500 on every
generate; fixed by a fresh `ollama pull`). On Jetson it's version alignment.
Mitigations: re-pull the model fresh; pin a JetPack-compatible ollama image;
`--no-gpu` is the guaranteed CPU fallback while you sort the pairing.

## Current state
- All fixes pushed to `origin/main` (latest `b9cd021`).
- WSL/k3d cluster was the dev sandbox; it's clean and disposable — ignore it.
- Demo workloads (`nginx-demo`) are seeded by the installer now, so use cases
  have a containment target.

## What "done" looks like on the Jetson
1. `kubectl get nodes -o jsonpath='{.items[0].status.allocatable.nvidia\.com/gpu}'` → `1`
2. `ollama ps` → **`100% GPU`** + a measured tok/s
3. `keosctl run lib-reverse-shell` completes in **seconds**, and `isolate_pod`
   labels `nginx-demo` + creates a deny-all NetworkPolicy (check
   `kubectl -n workloads get netpol,pods -l keos.pocteo/quarantine=true`)
4. Falco + Tetragon pods **Running** (not CrashLoop)

When you confirm the GPU path, save a memory with the working
JetPack/CUDA/ollama version triple + the tok/s number so the next install
doesn't rediscover it.
