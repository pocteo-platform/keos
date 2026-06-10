---
id: threat-model
slug: /security/threat-model
title: Threat model & scenarios
sidebar_position: 2
description: The Kubernetes runtime threat model the security domain defends against, and the four workshop scenarios that demonstrate it.
---

# Threat model & scenarios

The security domain is built around the Kubernetes runtime threat model — how
attackers operate **after** they bypass your perimeter (a zero-day in a
public-facing app, a compromised dependency, stolen credentials). Container
isolation is not a hard boundary: every pod on a node **shares the same Linux
kernel**, image scanners can't stop an attacker using legitimate tools at
runtime, and Kubernetes RBAC has **zero visibility into syscalls**.

## The vectors

| Vector | What it looks like at runtime | Keos scenario |
|---|---|---|
| **Container escape & privilege escalation** | mount the host fs / runtime socket, `privileged: true`, capability abuse (CAP_SYS_ADMIN), SUID binaries | `lib-priv-esc` |
| **Lateral movement** | east-west pivots across a flat cluster network | *(roadmap)* |
| **Cryptomining & resource abuse** | a miner (`xmrig`) dialing a pool, stealing CPU | `lib-crypto-miner` |
| **Data exfiltration** | outbound to an unknown IP, or DNS tunneling | `lib-data-exfil` |
| **Supply chain** | dormant in CI/CD, wakes at runtime, phones a C2 | *(roadmap)* |
| **Reverse shell** *(the canonical post-exploitation signal)* | interactive `/bin/sh -i` spawned inside a container | `lib-reverse-shell` |

## The four workshop scenarios

Each maps 1:1 to a security runbook (RAG-filtered to `domain=security`) and
fires either a **synthetic** event (dev box, no eBPF) or a **real attack
simulation** (workshop hardware — see [Workshop runbook](./workshop)).

### 1 · Reverse shell — `lib-reverse-shell`

An interactive shell spawns inside an app container. The strongest "we got
popped" signal. The real tell is **process ancestry**: a shell whose *parent
is the app process* (not an init system) — `proc.pname=nginx`,
`proc.cmdline=/bin/sh -i`. → **isolate_pod** (cut the C2 channel) +
`capture_forensics` + `page_security`.

### 2 · Crypto-miner — `lib-crypto-miner`

The most common post-exploitation payload — once an attacker has execution,
mining monetises it. A miner binary (`xmrig`) or a process dialing a pool.
→ **isolate_pod** (severing egress stops the mining) + capture the wallet/pool
IOCs + escalate.

### 3 · Privilege escalation / sensitive mount — `lib-priv-esc`

A container mounts `/var/run/docker.sock` (a direct host-escape primitive) or
gains `CAP_SYS_ADMIN`. → **isolate_pod** + `capture_forensics`, and escalate
with the **node flagged as potentially compromised** — an escape attempt puts
the host in scope, not just the pod.

### 4 · Data exfiltration / suspicious egress — `lib-data-exfil`

A pod opens an outbound connection to an unexpected external IP and pushes
data — the exfil / C2 stage. → **apply_network_policy** (deny-egress, DNS
excepted) cuts the leak **while keeping the pod observable** so IR can watch
the next move, + capture the destination IOCs + escalate.

## Process ancestry — the thing that separates signal from noise

The same binary can be benign or malicious depending on **who spawned it**:

- `curl` opened by the `nginx` binary serving traffic → legitimate.
- `curl` ← spawned by `bash` ← spawned by `nginx` → the web server was
  exploited into a reverse shell now pulling a payload.

The security diagnose prompt + runbooks key on exactly this lineage
(`proc.pname`, the parent chain), which is why a security incident's
confidence is high when the ancestry matches a known attack pattern and low
(→ escalate, don't contain) when it could be a legitimate `kubectl exec`.
