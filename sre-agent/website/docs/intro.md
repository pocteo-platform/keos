---
id: intro
slug: /
title: Welcome to Keos
sidebar_position: 1
sidebar_label: Welcome
description: Keos is an autonomous, policy-gated AI SRE agent for Kubernetes. This site is your operator handbook.
---

# Keos SRE Agent

**The Autonomous AI SRE Platform for Your Cloud-Native Infrastructure.**

Keos watches a Kubernetes cluster, diagnoses infrastructure incidents
with retrieval-augmented context, plans a single remediation action,
gates it through Open Policy Agent, executes under scoped RBAC, verifies
the cluster healed, and writes an HMAC-signed audit record — every time.

> The loop: **Observe → Predict → Heal → Learn**.

Built to port from a developer laptop to a fleet of edge clusters —
k3s on Jetson, Talos on small-footprint nodes, any conformant cluster
via Helm. CPU-only inference, multi-arch images, no GPU dependency.

## Where to start

| If you're… | Read this |
|---|---|
| **A new user** wanting to try it on your laptop | [Getting started → Quickstart](getting-started/quickstart) |
| **An architect** evaluating the design | [Architecture → Overview](architecture/overview) |
| **An operator** about to deploy to a cluster | [Platforms → Kubernetes via Helm](platforms/kubernetes-helm) |
| **On-call**, dealing with an incident | [Runbooks](runbooks/) |
| **Tuning behavior** | [Configuration → Environment variables](configuration/environment-variables) |

## What Keos is — and what it isn't

Keos **is**:

- A policy-gated decision loop. Every remediation passes through Rego policy
  *before* any side effect; the LLM never has the keys to mutate the cluster.
- A forensic audit chain. Every state transition is HMAC-signed and verifiable
  offline — tampering breaks the chain loudly.
- An edge-ready agent. CPU-only by design; the same chart runs on a laptop k3d
  cluster, a 3-node Talos cluster, or a Jetson Orin Nano with GPU acceleration.
- An open product surface. The 6-action catalog, the LangGraph state machine,
  the OPA matrix — all transparent and operator-editable.

Keos **is not**:

- A replacement for human SREs. It encodes patterns operators have already
  written; it doesn't invent new ones.
- A chat-for-kubectl. The interaction model is policy-gated automation, not
  free-form LLM action.
- An incident management platform. Page-out, ticketing, on-call rotation —
  Keos integrates with these via webhooks, doesn't replace them.

For the deeper comparison vs kagent / K8sGPT / Botkube / Cast AI, see
[POSITIONING.md](https://github.com/pocteo-platform/keos/blob/main/sre-agent/POSITIONING.md)
in the repo.

## Quick links

- **GitHub:** [pocteo-platform/keos](https://github.com/pocteo-platform/keos)
- **Release notes:** [v1.0.0-rc.1](release-notes/v1.0.0-rc.1)
- **Architecture diagrams:** [Architecture → Overview](architecture/overview)
- **Operational runbooks:** [Runbooks index](runbooks/)
- **Security policy:** [SECURITY.md](https://github.com/pocteo-platform/keos/blob/main/sre-agent/SECURITY.md)
- **Contributing:** [CONTRIBUTING.md](https://github.com/pocteo-platform/keos/blob/main/sre-agent/CONTRIBUTING.md)
