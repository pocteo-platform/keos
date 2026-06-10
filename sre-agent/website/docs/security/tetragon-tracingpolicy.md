---
id: tetragon-tracingpolicy
slug: /security/tetragon-tracingpolicy
title: Tetragon TracingPolicy
sidebar_position: 4
description: How the Keos security domain consumes Tetragon's eBPF TracingPolicy — hook points, selectors, actions — and the detection-first rollout that maps to observe-only → active.
---

# Tetragon TracingPolicy

[Tetragon](https://tetragon.io) compiles security logic into the Linux kernel
via **eBPF**, so it sees process execution, file access, and network
connections with kernel-level fidelity — and can **enforce inline** (kill a
process before its syscall completes). The Keos security domain consumes
Tetragon's events; on a Tetragon-enabled cluster the kernel-layer enforcement
sits *below* Keos's pod-level containment + forensics + escalation.

Keos ships an example policy at
[`deploy/tetragon/tracingpolicy-keos-security.yaml`](https://github.com/pocteo-platform/keos/blob/main/sre-agent/deploy/tetragon/tracingpolicy-keos-security.yaml).

## Anatomy

A `TracingPolicy` (cluster-wide) or `TracingPolicyNamespaced` CRD has three
parts:

1. **Hook point** — where Tetragon attaches its eBPF sensor:
   - **kprobes** — any kernel function (e.g. `security_bprm_creds_for_exec`
     on every execve, `tcp_connect` on every outbound connection, `sys_capset`
     on a capability change).
   - **tracepoints** — static, kernel-stable markers.
   - **uprobes** — user-space functions (e.g. hook OpenSSL to inspect data
     *before* it's encrypted).

2. **Selectors** — in-kernel BPF filtering, so irrelevant events are dropped
   **before** they ever reach user space:
   - `matchArgs` / `matchReturnArgs` — filter on syscall arguments.
   - `matchBinaries` / `matchParentBinaries` — restrict to specific execution
     chains. **This is process ancestry** — the difference between `sh` run by
     the app (suspicious) and by a debug sidecar.
   - `matchNamespaces` / `matchCapabilities` — catch a process trying to
     change its isolation boundary or elevate privileges.
   - Kubernetes label filters — apply only to specific workloads.

3. **Actions** — what Tetragon does on a match, synchronously in-kernel:
   - **`Post`** *(default)* — emit a JSON observability event. **Detection mode.**
   - **`NoPost`** — silently drop (filter known-safe noise).
   - **`Sigkill`** — kill the process in-kernel before the syscall completes.
     The cornerstone of enforcement.
   - **`Override`** — return an error (e.g. `EPERM`) to the caller, thwarting
     the action without crashing the container.

## The detection-first rollout = observe-only → active

> *"Apply a new policy with the POST action first. Monitor the output. Once a
> legitimate application is confirmed, switch the policy to enforcement mode."*
> — Cilium, *Kubernetes Runtime Security: The Guide*

This is **exactly** the Keos posture. The example policy ships every selector
in `action: Post` (detection). Each rule has a commented `Sigkill` / `Override`
block — uncomment to flip that rule to **in-kernel enforcement** once you've
confirmed it's safe, the same way you'd move Keos from `observe-only` to
`active`.

## Tetragon enforcement vs. NetworkPolicy

These operate at **different layers** and compose into zero-trust:

- **Cilium / Kubernetes NetworkPolicy** — the *network* layer (L3/L4). Keos's
  `apply_network_policy` writes one of these.
- **Tetragon runtime enforcement** — the *process* layer. It can dictate that
  *only the nginx process* in Pod A may connect to Pod B. If an attacker
  breaches Pod A and uses `netcat`, Tetragon blocks it **even though the
  NetworkPolicy allows pod-to-pod traffic**.

Keos's `isolate_pod` (deny-all NetworkPolicy) and `kill_pod` are the
orchestration-layer analogues; on a Tetragon cluster they complement the
in-kernel `Sigkill`, and the whole chain lands in the HMAC-signed audit trail.

## On the dev cluster

No eBPF? The `tetragon_observer` self-disables (one INFO line) and the
synthetic generator drives the demo:

```bash
keosctl run lib-reverse-shell    # and the other 3 scenarios
```
