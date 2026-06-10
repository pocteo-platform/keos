---
id: workshop
slug: /security/workshop
title: Workshop runbook
sidebar_position: 3
description: A presenter's runbook for demoing the Keos security domain live — what to say, the real kubectl exec attack commands, and the synthetic fallback when eBPF isn't available.
---

# Workshop runbook

A presenter's script for demoing the security domain live. Two paths:

- **Synthetic path** — works anywhere (kind, Docker Desktop, WSL). No eBPF.
  `keosctl run lib-*` fires a realistic security event; the agent does the
  full Detect → Investigate → Contain → Respond loop. **Use this if you're
  not certain the cluster has working Falco/Tetragon.**
- **Live-eBPF path** — on a real Linux cluster with Falco + Tetragon
  installed, you perform the *actual* attack and the telemetry detects it for
  real. More convincing, but eBPF in kind-on-Docker-Desktop-Windows is
  unreliable — reserve this for workshop hardware.

:::tip Decide up front
Run `keosctl deps list`. If **Falco** and **Tetragon** both show ✓, use the
live path. Otherwise use the synthetic path — it tells the same story.
:::

## Setup (once, before the room)

```bash
# Security telemetry (live path only — skip on the dev box)
keosctl deps install falco
keosctl deps install tetragon
kubectl apply -f deploy/tetragon/tracingpolicy-keos-security.yaml

# Put the agent in active mode so it can actually contain
keosctl mode active

# Open the SPA on the Pipeline + Security pages
keosctl ui
```

## The four beats

For each scenario: **say the threat → fire it → watch the pipeline →
show the audit trail.** Keep the SPA's Pipeline tab visible — the timeline
animates **Detect → Investigate → Contain → Respond**.

### Beat 1 — Reverse shell

> *"An attacker just popped a shell inside our checkout pod. Watch Keos see
> it, isolate the pod, and page incident response — with a signed audit
> trail."*

**Synthetic:**
```bash
keosctl run lib-reverse-shell
```

**Live (real detection):**
```bash
# Falco's "Terminal shell in container" rule fires for real:
kubectl exec -n workloads deploy/nginx-demo -- /bin/sh -i
```

Then:
```bash
keosctl events --domain security -n 3     # SEC-tagged, isolate_pod, escalated
keosctl audit verify                       # the chain, including this incident
```

### Beat 2 — Crypto-miner

> *"The most common payload after a breach: a miner stealing your CPU and
> dialing a pool. Isolating the pod severs the pool connection instantly."*

```bash
keosctl run lib-crypto-miner               # synthetic
# live: run a process named xmrig inside the pod (the attack-sim Job does this)
keosctl runbooks show security/002-crypto-miner
```

### Beat 3 — Privilege escalation

> *"A container just mounted the container-runtime socket — a direct path to
> escape onto the host. Keos contains the pod AND flags the node."*

```bash
keosctl run lib-priv-esc                   # synthetic
keosctl policy test isolate_pod critical workloads active     # ALLOW
keosctl policy test isolate_pod critical kube-system active   # DENY (protected)
```

### Beat 4 — Data exfiltration

> *"A pod is pushing data to an unknown IP. The right move cuts the leak but
> keeps the pod observable, so IR can watch the attacker's next move."*

```bash
keosctl run lib-data-exfil                 # synthetic → apply_network_policy
keosctl events -n 6                        # both domains side by side
```

## The closing line

> *"Same product, same install, same audit chain — one agent reasoning across
> both reliability and security. The kernel-layer enforcement (Tetragon's
> in-kernel `Sigkill`) stops the syscall; Keos adds the diagnosis, the
> cross-resource containment, the forensics, and the human hand-off."*

## If something goes sideways

| Symptom | Fix |
|---|---|
| `keosctl run` says API down | `keosctl status` → `keosctl restart api --wait` |
| Live attack doesn't fire an incident | Falco/Tetragon not wired — fall back to the synthetic `keosctl run lib-*` |
| Incident escalates instead of containing | You're in observe-only — `keosctl mode active` (this is correct, safe behaviour) |
| No SEC tag on the incident | The event came in as SRE — confirm you ran a `lib-*` **security** scenario (`keosctl usecases list --domain security`) |
