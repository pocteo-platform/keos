---
id: quickstart
slug: /getting-started/quickstart
title: Quickstart
sidebar_position: 3
description: Three commands, an interactive wizard, a running agent on your Kubernetes cluster.
---

# Quickstart

The fastest path from zero to a running agent — for the two scenarios operators actually hit.

## Scenario A — you have a Kubernetes cluster

**Three commands. An interactive wizard handles the rest.**

```bash
git clone https://github.com/pocteo-platform/keos
cd keos
bash sre-agent/scripts/keosctl install
```

What happens:

1. **Wizard banner + step 1/6 — Prerequisites.** Checks `kubectl`, `helm`, `docker`, `openssl`, `python3`, `curl`. Missing tools are flagged.
2. **Step 2/6 — Cluster.** Lists every `kubectl` context with live reachability probe. Pick a number or paste a kubeconfig path.
3. **Step 3/6 — Discovered facts.** Distro (k3s / Talos / Kairos / kubeadm), runtime, target node, GPU. Confirm or override.
4. **Step 4/6 — LLM model.** Pick from 4 sized options: 1.5B (fast, low-mem), **3B (recommended)**, 7B (highest quality), Llama 3.2 3B.
5. **Step 5/6 — Image source.** Probes GHCR for public visibility, auto-flips to `--local-build` if private.
6. **Step 6/6 — Agent default mode.** `observe-only` (safe), `active` (mutates), `frozen` (no-op).
7. **Review** — table summary of every chosen value, asks to proceed.
8. **Install runs** — live output, all 13 pods come up, models pull (~5-10 min on first run), runbook corpus seeds.
9. **Done** — bearer token printed, `keosctl` symlinked to `~/.local/bin/`, list of daily commands.

After install, everything is `keosctl <verb>`:

```bash
keosctl status            # release + pods + agent mode
keosctl ui                # port-forward + open the SPA in your browser
keosctl mode active       # let the agent take action
keosctl events --watch    # stream incidents
keosctl help              # 23 commands, 5 sections
```

For the full CLI reference, see [the CLI section](../cli/).

## Scenario B — you just want a laptop demo

No K8s cluster needed; spins up the full stack via `docker compose` + a local `k3d` cluster:

```bash
git clone https://github.com/pocteo-platform/keos
cd keos/sre-agent
make install              # Windows: .\make.ps1 install
make demo-ui              # opens http://localhost in your browser
```

On a cold machine this takes about **10 minutes**, mostly the LLM model download. Subsequent `install` runs skip every cached step.

What `make install` does (more detail in [Installation](./installation.md)):

1. Provisions missing prereqs (`uv`, `k3d`, `opa`, `helm`) into `~/.local/bin` — no sudo.
2. Pulls `qwen2.5:3b-instruct-q4_K_M` (~1.9 GB) + `nomic-embed-text` (~270 MB) into Ollama.
3. Brings up the 9-service docker-compose stack.
4. Creates the `k3d` cluster + applies the RBAC + CRDs.
5. Helm-installs Chaos Mesh.
6. Seeds the 12 runbooks into Qdrant.
7. Runs `doctor` to verify everything's green.

The laptop demo + `keosctl` aren't exclusive: after `make install`, `keosctl` works against the laptop's `k3d` cluster too. The `make demo-ui` target is just a convenience that adds browser auto-open + a scenario auto-fire 5 s after launch.

## Verify it works

Either scenario should end with:

```bash
$ keosctl status
… 13/13 pods Ready

$ keosctl doctor
…
  All checks passed.
```

## Your first scenario

With `keosctl mode active` flipped on:

```bash
keosctl run lib-memory-leak
keosctl events --watch       # in a second terminal
keosctl ui                   # in a third, watch the Pipeline tab animate
```

On Jetson Orin GPU: ~30 s end-to-end. On CPU-only laptop: 3-5 min. The agent diagnoses, picks `restart_pod`, OPA allows (we're in `active` + namespace is `workloads`), `verify` confirms recovery → outcome `healed`.

## Next steps

- [CLI overview](../cli/) — every keosctl command, what it does, examples
- [Architecture overview](../architecture/overview.md) — the 9-node graph, the OPA matrix, the audit chain
- [Your first scenario, in depth](./your-first-scenario.md) — what each pipeline node actually does
- [Platform guides](../platforms/jetson.md) — Jetson + Kairos / Talos / k3s / EKS specifics
