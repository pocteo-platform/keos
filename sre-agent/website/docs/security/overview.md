---
id: overview
slug: /security/overview
title: Overview — the security domain
sidebar_position: 1
description: Keos as one platform with two agent domains — SRE reliability and runtime security — sharing the OPA gate, HMAC audit chain, and Observe/Detect loop.
---

# The Keos security domain

Keos started life as a runtime-**security** agent, pivoted to **SRE**, and now
runs **both as two domains over one graph**. Same codebase, same `keosctl`,
same install, same HMAC-signed audit chain — a second threat-response facet.

> **The layered model.** This domain pairs with kernel-level eBPF tooling, not
> replaces it. [Tetragon](https://tetragon.io) and [Falco](https://falco.org)
> watch (and can enforce) at the **kernel/syscall layer**; the Keos security
> agent adds the layer above — **LLM diagnosis, cross-resource containment,
> forensics capture, and human escalation with a signed audit trail.**

## Two domains, one agent

| | SRE domain | Security domain (Phase 21) |
|---|---|---|
| Loop | Observe → Predict → Heal → Learn | **Detect → Investigate → Contain → Respond** |
| Telemetry | kubelet events, Chaos Mesh | **Falco + Tetragon** (eBPF) |
| Event bus | `infra.events.*` | `security.events.*` |
| Tools | restart_pod, rollout_undo, cordon_node… | `isolate_pod`, `apply_network_policy`, `kill_pod`, `quarantine_pod`, `capture_forensics`, `page_security` |
| Posture | heal autonomously | **escalate-first, gated auto-contain** |

The discriminator is one field — `domain` — set at triage from the event
source (`falco`/`tetragon` → security). It branches the graph at exactly
three nodes (which prompt, which tool catalog, which RAG filter); everything
else — the OPA gate, the audit chain, the SPA, the CLI — is shared.

## Why runtime security at all

Pre-deployment image scanning is *predictive*: it only catches what's already
in a CVE database. It can't see a zero-day, a stolen credential, a supply-chain
dependency that wakes up at runtime, or an attacker using legitimate tools
(`curl`, `bash`) to pull malware into memory. **Runtime security picks up
where CI/CD ends** — watching syscalls, process lineage, and network behaviour
as code actually executes. (See Cilium's
[Kubernetes Runtime Security: The Guide](https://cilium.io/blog/2026/06/07/kubernetes-runtime-security-the-guide/),
which this domain's design follows closely.)

## Escalate-first posture = the textbook rollout

The guide's recommended Tetragon rollout — *"apply with the POST (detection)
action first, monitor, then switch to enforcement"* — **is exactly our
observe-only → active model**:

- **observe-only** (default): detect → diagnose → `page_security`. No mutation.
- **active**: containment (`isolate_pod`, `apply_network_policy`, `kill_pod`)
  is allowed — but only at `high`/`critical`, only outside `kube-system`, and
  only through the OPA matrix. `isolate_pod` is **reversible** (delete the
  NetworkPolicy + label).

## Run it

On the dev cluster (no eBPF needed — synthetic events):

```bash
keosctl mode active
keosctl run lib-reverse-shell      # → isolate_pod + escalate
keosctl run lib-crypto-miner
keosctl run lib-priv-esc
keosctl run lib-data-exfil
keosctl events --domain security   # the 4 incidents, SEC-tagged
keosctl audit verify               # chain intact incl. security incidents
```

On real workshop hardware (Linux cluster with working eBPF), see the
[Workshop runbook](./workshop) for the live `kubectl exec` attack commands.
