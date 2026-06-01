---
id: intro
slug: /
title: Welcome
sidebar_position: 1
sidebar_label: Welcome
description: The autonomous SRE agent that ships inside every Keos edge node — by Pocteo Platform.
---

# Keos SRE Agent

**The autonomous SRE that lives inside every Keos edge node.**

## How the pieces fit

```text
Pocteo Platform        ← the company
        │
        └── Keos       ← the Kubernetes edge OS (this is the product)
              │
              └── Keos SRE Agent   ← the autonomous reliability layer that
                                     runs INSIDE every Keos edge cluster
                                     (this site documents it)
```

- **Pocteo Platform** is the company building edge-native infrastructure.
- **Keos** is Pocteo's Kubernetes operating system for the edge — designed
  to run on Jetson, Talos, k3s-on-tiny-hardware, anywhere a full
  control plane fits in <2 GB of RAM.
- The **Keos SRE Agent** (what this site is about) is a policy-gated
  autonomous SRE that ships inside every Keos edge node. It watches the
  local cluster, diagnoses infrastructure incidents with retrieval-augmented
  context, plans a single remediation, gates it through Open Policy Agent,
  executes under scoped RBAC, verifies the cluster healed, and writes an
  HMAC-signed audit record — every time.

> The loop: **Observe → Predict → Heal → Learn**.

Built to port from a developer laptop to a fleet of edge clusters —
k3s on Jetson, Talos on small-footprint nodes, any conformant cluster
via Helm. CPU-only inference, multi-arch images, no GPU dependency.

## Where to start

| If you're… | Read this |
|---|---|
| **A new user** wanting to try it on your laptop | [Getting started → Quickstart](/docs/getting-started/quickstart) |
| **An architect** evaluating the design | [Architecture → Overview](/docs/architecture/overview) |
| **An operator** about to deploy to a cluster | [Platforms → Kubernetes via Helm](/docs/platforms/kubernetes-helm) |
| **On-call**, dealing with an incident | [Runbooks](/docs/runbooks/) |
| **Tuning behavior** | [Configuration → Environment variables](/docs/configuration/environment-variables) |

## What the Keos SRE Agent is — and what it isn't

The agent **is**:

- A policy-gated decision loop. Every remediation passes through Rego policy
  *before* any side effect; the LLM never has the keys to mutate the cluster.
- A forensic audit chain. Every state transition is HMAC-signed and verifiable
  offline — tampering breaks the chain loudly.
- Edge-ready by design. CPU-only by default; the same chart runs on a laptop k3d
  cluster, a 3-node Talos cluster, or a Jetson Orin Nano with GPU acceleration.
- An open product surface. The 6-action catalog, the LangGraph state machine,
  the OPA matrix — all transparent and operator-editable.

The agent **is not**:

- A replacement for human SREs. It encodes patterns operators have already
  written; it doesn't invent new ones.
- A chat-for-kubectl. The interaction model is policy-gated automation, not
  free-form LLM action.
- An incident management platform. Page-out, ticketing, on-call rotation —
  the agent integrates with these via webhooks, doesn't replace them.
- The Keos edge OS itself — that's the underlying platform this agent runs on.

For the deeper comparison vs kagent / K8sGPT / Botkube / Cast AI, see
[POSITIONING.md](https://github.com/pocteo-platform/keos/blob/main/sre-agent/POSITIONING.md)
in the repo.

## Quick links

- **GitHub:** [pocteo-platform/keos](https://github.com/pocteo-platform/keos)
- **Release notes:** [v1.0.0-rc.1](/docs/release-notes/v1.0.0-rc.1)
- **Architecture diagrams:** [Architecture → Overview](/docs/architecture/overview)
- **Operational runbooks:** [Runbooks index](/docs/runbooks/)
- **Security policy:** [SECURITY.md](https://github.com/pocteo-platform/keos/blob/main/sre-agent/SECURITY.md)
- **Contributing:** [CONTRIBUTING.md](https://github.com/pocteo-platform/keos/blob/main/sre-agent/CONTRIBUTING.md)
