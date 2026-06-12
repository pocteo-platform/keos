---
id: user-guide
slug: /user-guide
title: User & Operator Guide
sidebar_position: 1
description: Operate Keos day to day — the agent loop, the kill-switch & safety model, the action catalog, the web UI, the use-case library, day-2 tasks, configuration, security operations, and the audit trail.
---

# User & Operator Guide

**Pocteo Platform** is the company; **Keos** is its Kubernetes edge OS; the
**Keos SRE Agent** (this product) is the autonomous reliability + security layer
that runs inside every edge node. This guide takes you from "what is it" to
"running it day to day." For the internals see [Architecture](./ARCHITECTURE.md);
to install see [Installation](./INSTALLATION.md); for every command see the
[CLI Reference](./CLI_REFERENCE.md).

## 1. What it is (and isn't)

Keos takes operational incidents from **telemetry → diagnosis → remediation →
verify** without an on-call human for the majority of known failure modes —
memory leaks, crashloops, ImagePullBackOff, node pressure, bad rollouts, config
drift, traffic surges (SRE), and reverse shells, miners, priv-esc, exfil
(security). It runs the loop **Observe → Predict → Heal → Learn** in-cluster, on
the edge, with no cloud dependency.

It is **not** a chat-for-kubectl, not a dashboard, and not a replacement for
your whole SRE org — it automates the known-knowns under a hard policy gate and
escalates everything else.

## 2. The agent loop

Each incident runs the nine-node graph (full detail in
[Architecture §2](./ARCHITECTURE.md#2-the-self-healing-loop-the-agent-graph)):
triage → retrieve context (RAG) → K8sGPT enrich → **diagnose** → plan action →
**OPA gate** → execute → verify → (escalate if low-confidence / denied /
unhealed). Watch it live in the SPA's **Pipeline** view or with
`keosctl events -w`.

## 3. The kill-switch & safety model

The agent's autonomy is controlled by the **AgentMode** CRD — the single most
important operator control:

```bash
keosctl mode                 # show current mode
keosctl mode observe-only    # dry-run: diagnose + propose, never mutate (default)
keosctl mode active          # act after the OPA gate allows
keosctl mode frozen          # no-op (maintenance windows)
```

Mode is read at the moment of execution, so flipping it takes effect on the next
(or current) action. Behind it sit three more controls — the **OPA matrix**
(severity × action), **scoped RBAC**, and the **HMAC audit chain**. Together
they guarantee the agent can only do allowed, reversible things, and that every
action is recorded and signed. See [Architecture §3](./ARCHITECTURE.md#3-the-safety-mesh).

## 4. Action catalog

SRE: `restart_pod`, `rollout_undo`, `scale_deployment`, `cordon_node`/
`uncordon_node`, `drain_node`, `evict_workload`, `tune_hpa`, `set_resources`,
`revert_drift`, `page_human`. Security: `isolate_pod`, `apply_network_policy`,
`quarantine_pod`, `kill_pod`, `capture_forensics`, `page_security`. Inspect or
test the gate:

```bash
keosctl policy show
keosctl policy test isolate_pod high workloads active     # → ALLOW
keosctl policy test isolate_pod high kube-system active    # → DENY (protected ns)
```

## 5. The web UI

`keosctl ui` port-forwards and opens the SPA (paste the token from
`keosctl token`). Six views:

| View | What it's for |
|---|---|
| **Cluster** | Live nodes / namespaces / pods. |
| **Incidents** | Every incident, filterable; drill into the 8 tabs (Falco event, RAG, K8sGPT, LLM, policy, action, audit chain, pipeline replay). |
| **Pipeline** | The agent graph animating live, or replayed for any past incident. |
| **Use Cases** | The scenario library + the creator wizard. |
| **Control** | Flip the agent mode; approve/deny pending actions; browse the audit log. |
| **Settings** | Calibration, retention, language (EN/FR). |

## 6. Use cases (drive the agent)

The use-case library fires realistic incidents so you can watch the agent act:

```bash
keosctl usecases list                 # sre + security scenarios
keosctl mode active
keosctl run lib-reverse-shell         # security: → isolate_pod + quarantine
keosctl run lib-crypto-miner          # security
keosctl events --domain security      # see the outcomes
```

Create your own in the SPA's **Use Cases → New** wizard (target, Falco/event
template, expected outcome, EN/FR runbook). Use cases that target real demo
workloads (`nginx-demo`, seeded at install) produce real containment you can
inspect and reverse (`keosctl quarantine release`).

## 7. Day-2 operations

```bash
keosctl status                 # pods + mode + release
keosctl doctor [--e2e]         # full health check (+ live security path)
keosctl top                    # one-screen live dashboard
keosctl logs api -f            # tail a component
keosctl models pull qwen2.5:7b-instruct-q4_K_M   # upgrade the LLM
keosctl backup -o keos.tar.gz  # snapshot state
keosctl upgrade                # helm upgrade --reuse-values
```

Release, private-registry, GitOps, GPU verification, observability, and DR live
in [Operations](./OPERATIONS.md).

## 8. Security operations

Keos runs a parallel runtime-security domain (Falco/Tetragon → detect →
contain). The full picture — sensor tiers, the detection→containment flow, the
containment catalog, and the security policy gate — is in
**[Security Domain](./SECURITY.md)**. When the eBPF sensors are installed, real
detections become incidents. Confirm the path end to end, then operate it:

```bash
keosctl doctor --e2e           # triggers a shadow-read; asserts an incident appears
keosctl quarantine list        # contained pods + their deny-all NetworkPolicies
keosctl quarantine release <pod> <ns>      # undo containment
```

Sensor tiers (BTF / prebuilt-probe / off) are chosen at install — see
[Installation → Security sensors](./INSTALLATION.md#security-sensors-falco--tetragon).

## 9. The audit trail

Every agent action is one HMAC-signed line. This is your forensic record:

```bash
keosctl audit tail -n 40
keosctl audit verify           # walks the chain; exits non-zero on any tamper
```

If verification fails, treat it as a security event — see the
[audit-tampering runbook](./runbooks/audit-tampering.md).

## 10. Configuration reference

Everything is a Helm value (`deploy/helm/pocteo/values.yaml`), readable/patchable
live:

```bash
keosctl values get                         # full effective values
keosctl values get agent.defaultMode
keosctl values set agent.confidenceThreshold=0.7
```

Key knobs: `agent.defaultMode`, `agent.confidenceThreshold`, `ollama.gpu.enabled`,
`falco.enabled`/`tetragon.enabled`, `networkPolicy.enabled`, `monitoring.enabled`,
`retention.{auditDays,incidentDays,traceDays}`, `global.imageRegistry`,
`auth.existingSecret` (GitOps/SOPS). Auth tokens come from the auth secret (or a
SOPS-managed one under GitOps).

## 11. Troubleshooting

`keosctl doctor` first — it names the fix for each failing check. For specific
failures (api/ollama/nats down, qdrant corruption, runaway graph, drift storm,
the agent took a destructive action, secret rotation, full DR) see the
[Runbooks](./runbooks/README.md).

## FAQ

- **Will it touch `kube-system`?** No — protected namespaces are hard-denied.
- **Can I stop it instantly?** `keosctl mode frozen`.
- **Does it phone home?** No. Inference + retrieval are in-cluster.
- **Can I undo a containment?** Yes — `keosctl quarantine release` (all
  containment except `kill_pod` is reversible).
- **GPU required?** No — CPU works; GPU just makes the loop seconds instead of
  minutes.
