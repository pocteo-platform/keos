---
id: security
slug: /security
title: Security Domain (Falco + Tetragon)
sidebar_position: 6
description: The runtime-security half of Keos — Falco/Tetragon eBPF detection, the capability ladder, the detect → investigate → contain → respond flow, the containment catalog + OPA gating, and how to operate it.
---

# Security Domain (Falco + Tetragon)

Keos runs **two agent domains over one graph**: SRE self-healing
(`infra.events.*`) and **runtime security** (`security.events.*`). Both share
the OPA gate, the kill-switch, and the HMAC audit chain. This page is the
security half — for the loop mechanics see [Architecture](./ARCHITECTURE.md),
for installing the sensors see
[Installation → Security sensors](./INSTALLATION.md#security-sensors-falco--tetragon).

> Keos sits **above** Falco/Tetragon, it doesn't replace them. Tetragon
> enforces in-kernel (it can kill a malicious process mid-syscall); Keos adds
> **LLM diagnosis over process ancestry, OPA-gated cross-resource containment,
> forensics, and signed-audit escalation**. The recommended detection-first →
> enforcement rollout is exactly Keos's `observe-only → active` posture.

## The detect → investigate → contain → respond flow

```
Falco / Tetragon (eBPF) ─► falcosidekick ─►(Bearer token)─► api /api/webhooks/falco
        detect                forward                          │
                                                               ▼  security.events.<rule>
                                            triage (domain=security) → retrieve → diagnose
                                                               │
                                                               ▼  plan: isolate_pod / quarantine_pod / …
                                                          OPA gate (security matrix)
                                                               │ allow (active + high/critical)
                                                               ▼
                                       execute: deny-all NetworkPolicy + quarantine label
                                                               │
                                                               ▼  verify → signed audit → page_security
```

A detection becomes a `security.events.*` incident, runs the same nine-node
graph with `domain=security`, and — unlike an SRE event — is **never skipped
for the pod being healthy** (a reverse shell lives happily in a Running pod).

## Sensor capability ladder

eBPF needs a capable kernel. The installer picks the tier and records it:

| Tier | When | Behaviour |
|---|---|---|
| `modern_ebpf` | kernel has BTF (most stock distros ≥5.8, Talos) | Falco CO-RE probe + **Tetragon on**; nothing to compile |
| `ebpf-prebuilt` | no BTF (Jetson L4T, locked-down kernels) | legacy Falco probe built once per kernel via `keosctl falco-probe build`; Tetragon off |
| `off` | WSL2 / incapable kernel | sensors disabled; the synthetic use-case path still demonstrates the full loop |

On a Jetson: `sudo REGISTRY=<reg> keosctl falco-probe build` on the node (once
per kernel), then install. Re-run after a kernel upgrade.

## Delivery: why falcosidekick

Falco's own `http_output` **cannot attach an `Authorization` header**, and the
agent webhook (`/api/webhooks/falco`) fails **closed** without a bearer token.
So detections go through **falcosidekick**, which forwards each alert to the api
with the token. The installer generates `FALCO_WEBHOOK_TOKEN`, stores it in the
auth secret (the api reads it via `envFrom`), and wires falcosidekick's
`customHeaders`. Confirm the whole path end to end:

```bash
keosctl doctor --e2e     # triggers a shadow read; asserts an incident appears
```

## The containment catalog

| Tool | Effect | Reversible |
|---|---|---|
| `isolate_pod` | quarantine label + **deny-all** (ingress+egress) NetworkPolicy | ✅ |
| `apply_network_policy` | deny-**egress** NetworkPolicy (suspected exfil) | ✅ |
| `quarantine_pod` | isolate + an evidence-hold annotation (forensics) | ✅ |
| `capture_forensics` | annotate + dump (read-only) | n/a |
| `kill_pod` | force-delete the pod | ❌ (kills process state) |
| `page_security` | escalate to the IR channel (never gated) | n/a |

Reverse any containment with `keosctl quarantine release <pod> <ns>` (deletes
the NetworkPolicy + strips the label).

## OPA gating for security actions

Security tools use the same Rego severity × action matrix as SRE
(see [Architecture → Policy gate](./ARCHITECTURE.md#policy-gate-the-opa-matrix)).
The posture is **escalate-first**: containment is **denied on weak signals**
(low/medium = L3) and **allowed on confident ones** (high/critical = L1/L2),
and only ever in `active` mode, never in protected namespaces.

```
isolate_pod / apply_network_policy / quarantine_pod : low L3 · medium L3 · high L2 · critical L1
kill_pod (irreversible)                              : low L4 · medium L4 · high L3 · critical L2
capture_forensics / page_security                    : L1 everywhere (always allowed)
```

So a `high`/`critical` reverse-shell in `active` mode auto-contains; a `low`
signal pages a human instead. Test any decision:

```bash
keosctl policy test isolate_pod high workloads active     # ALLOW
keosctl policy test isolate_pod low  workloads active     # DENY (L3 → escalate)
keosctl policy test isolate_pod high kube-system active   # DENY (protected ns)
```

## Threat scenarios (the use-case library)

```bash
keosctl mode active
keosctl run lib-reverse-shell     # shell spawned in a container
keosctl run lib-crypto-miner      # miner signature
keosctl run lib-priv-esc          # privilege escalation
keosctl run lib-data-exfil        # outbound exfil → apply_network_policy
keosctl events --domain security  # the SEC-tagged incidents + outcomes
```

These target the seeded `nginx-demo` workload, so containment is **real** and
inspectable (`keosctl quarantine list`). On a kernel without eBPF the synthetic
path still drives the full diagnose → gate → contain → audit loop.

## Operating it

```bash
keosctl quarantine list                  # contained pods + their deny-all NetworkPolicies
keosctl quarantine release <pod> <ns>    # undo containment
keosctl events --domain security -w      # live security incident stream
keosctl audit verify                     # the same signed chain covers security actions
```

## Known limits

- **Tetragon needs BTF** — impossible on kernels without it (e.g. stock Jetson
  L4T) until NVIDIA ships a BTF kernel; Falco still runs via the prebuilt probe.
- **The Falco probe is kernel-bound** — rebuild after a kernel upgrade.
- **Detection quality is Falco/Tetragon's** — Keos adds the diagnosis,
  gating, containment, and audit on top; tune the underlying rules upstream.
