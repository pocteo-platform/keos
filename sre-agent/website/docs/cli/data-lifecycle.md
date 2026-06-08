---
id: data-lifecycle
slug: /cli/data-lifecycle
title: Data lifecycle
sidebar_position: 7
description: keosctl backup · restore · diag — production-grade backup, restore, and support-bundle generation for the Keos SRE Agent.
---

# Data lifecycle

Three commands for the production lifecycle of the agent's persistent state: `backup`, `restore`, and `diag` (support bundle).

All three are namespace-scoped and run against the currently selected kubeconfig context. Backups are reproducible: same install + same data = byte-equivalent tarball.

## `keosctl backup` — full state snapshot

```bash
keosctl backup
```

Creates a tarball named `keos-backup-YYYYMMDD-HHMMSS.tar.gz` in the current directory containing:

| Path inside the tarball | What it is |
|---|---|
| `META` | Backup version, timestamp, namespace, release name |
| `helm-values.yaml` | Full `helm get values -a` of the release (every effective value, including computed defaults) |
| `agentmode.yaml` | The `AgentMode` CR — captures the current mode + reason |
| `audit.log` | The HMAC-signed audit chain from the api pod |
| `qdrant-snapshots/` | A fresh Qdrant snapshot of the `runbooks` collection (created on the fly) |
| `manifests.yaml` | All `kubectl get` output (Deployments, Services, ConfigMaps, Secrets, PVCs, NetworkPolicies, PDBs) |
| `ollama-models/` | (only if `--include-models`) full Ollama model directory — can be GB |

### Flags

- `-o`, `--output <path>` — explicit output filename
- `--include-models` — bundle Ollama weights too (default `false`; can add several GB)

```bash
keosctl backup -o /mnt/backup/keos-2026-06-09.tar.gz
keosctl backup --include-models -o /mnt/backup/keos-full.tar.gz
```

### What to back up where

| Asset | Frequency | Storage |
|---|---|---|
| Audit chain + AgentMode + helm values | **daily** | Cheap (KB) — back to S3 / B2 / NFS |
| Qdrant snapshot | **weekly** or post-seed change | Few MB |
| Ollama models | **once per upgrade** | Several GB — separate backup target |
| Full bundle (everything) | before each helm upgrade | Production-grade rollback insurance |

## `keosctl restore` — restore from a backup

```bash
keosctl restore keos-backup-20260608-180000.tar.gz
```

Restores into the **currently selected namespace + release**. Assumes the chart is already installed (run `keosctl install` first if not).

What it does, in order:

1. **AgentMode CR** — `kubectl apply -f agentmode.yaml`
2. **Audit log** — `kubectl cp` into the api pod
3. **Qdrant snapshots** — `kubectl cp` into the qdrant pod (you'll then need to run the Qdrant `recover` API call yourself; the command prints the exact curl line)
4. **Ollama models** — only if the backup included them; `kubectl cp` into the ollama pod

After restore:

```bash
keosctl doctor
```

If `doctor` says runbook corpus is empty, run `keosctl seed` (the restore preserves the `audit.log` and helm values but not the Qdrant collection itself — only the snapshots).

### What `restore` does NOT touch

- Helm values: a restore on top of a different helm revision doesn't override the running config. If you need to roll back the helm release too: `helm rollback keos <revision>` first, then `keosctl restore`.
- Secrets (bearer token, audit HMAC key): backup excludes these for safety. Re-fetch via `keosctl token` after restore.
- Cluster-scoped resources (CRDs, ClusterRoles): assumed already present from the install.

## `keosctl diag` — support bundle

Generates a comprehensive diagnostic tarball for filing GitHub issues or escalating support tickets. Includes:

| Path inside the tarball | What it captures |
|---|---|
| `META` | Generation timestamp, namespace, release |
| `cluster/` | Full `kubectl cluster-info dump` |
| `nodes.yaml` | Node descriptions + capacity |
| `k8s-version.txt` | Cluster version |
| `ns-all.yaml` | Every resource in the Keos namespace |
| `pods-describe.txt` | `kubectl describe pod` for each pod (events, init containers, resource allocations) |
| `helm-all.yaml` | `helm get all` — values + manifest + hooks + notes |
| `logs/<component>.log` | Last 500 lines of each of the 8 components |
| `agentmode.yaml` | The AgentMode CR |
| `audit-tail.jsonl` | Last 100 audit records (the chain head) |
| `doctor.txt` | Full `keosctl doctor` output |
| `versions.txt` | `keosctl version` |

```bash
keosctl diag
# → keos-diag-20260609-084214.tar.gz
keosctl diag /tmp/issue-1234.tar.gz
```

When filing an issue:

```bash
keosctl diag
gh issue create --title "OPA timeout on every diagnose" \
                --body "Attached: keos-diag-…tar.gz" \
                --label bug
gh issue comment $(gh issue list -L 1 --json number -q '.[0].number') \
                -F keos-diag-*.tar.gz
```

The bundle is **safe to attach publicly** — no secrets, no bearer tokens, no private keys. Audit records do contain incident metadata (pod names, namespaces, rules), which is usually fine to share for debugging but check your org's policy first.

## Common scenarios

### "Backup before upgrade"

```bash
keosctl backup -o backup-pre-1.0.2.tar.gz
keosctl upgrade --chart-version 1.0.2
keosctl doctor

# If upgrade misbehaved:
helm rollback keos
keosctl restore backup-pre-1.0.2.tar.gz
```

### "Cron-driven daily backup, 7-day retention"

```bash
# /etc/cron.daily/keos-backup
#!/bin/bash
set -e
cd /var/lib/keos-backups
keosctl backup -o "$(date +%Y-%m-%d).tar.gz" --no-color
find . -name '*.tar.gz' -mtime +7 -delete
```

### "Side-loading state from prod → staging"

```bash
# On prod:
keosctl backup -o prod-snapshot.tar.gz

# On staging:
KEOS_NAMESPACE=keos-staging KEOS_RELEASE=keos-staging \
  keosctl restore prod-snapshot.tar.gz
```

### "I broke something — file an issue"

```bash
keosctl diag
# Attach the resulting tarball to the GitHub issue.
```

## What lives where (mental model)

| Component | State | Backed by |
|---|---|---|
| api | `/app/audit.log` (HMAC chain) | `audit.log` in backup |
| qdrant | `runbooks` collection on PVC | `qdrant-snapshots/` in backup |
| ollama | `/root/.ollama` model files on PVC | `--include-models` flag |
| nats | JetStream on PVC | implicit (event replay) — not in backup |
| AgentMode CR | etcd | `agentmode.yaml` in backup |
| Helm release | etcd | `helm-values.yaml` in backup |

NATS JetStream is intentionally **excluded** from backup: the agent's design treats events as transient, and audit log + incident store already capture the durable parts. If you do need to preserve in-flight messages across a restore, snapshot the NATS PVC manually with `velero` or `kubectl cp`.

## See also

- [Operations](./operations) — `status`, `doctor`, `logs`
- [Install + lifecycle](./install-uninstall) — `install`, `uninstall`, `upgrade`
- [Backup & restore runbook](../operations/backup-restore.md) — detailed disaster recovery procedure
