---
id: helm-values
slug: /configuration/helm-values
title: Helm values
sidebar_position: 2
description: Every key in deploy/helm/pocteo/values.yaml documented with default, type, and effect.
---

# Helm values reference

Every behavior knob in the Helm chart, grouped by domain. Defaults are
laptop-safe but conservative — production users will override `auth.*`,
`networkPolicy`, `monitoring`, and `backup` at minimum.

The source of truth is
[`deploy/helm/pocteo/values.yaml`](https://github.com/pocteo-platform/keos/blob/main/sre-agent/deploy/helm/pocteo/values.yaml).

## auth — bearer token + HMAC key

| Key | Type | Default | Notes |
|---|---|---|---|
| `auth.apiToken` | string | `"replace-me"` | The bearer token the SPA + API client uses. **MUST** be replaced before production. 32+ bytes of entropy recommended. |
| `auth.hmacKey`  | string | `"replace-me"` | The HMAC key signing the audit chain. **MUST** be replaced. Rotating it breaks pre-rotation chain verification — see [secret rotation](../runbooks/secret-rotation). |

```yaml
auth:
  apiToken: "<openssl rand -base64 32>"
  hmacKey:  "<openssl rand -base64 32>"
```

## security — pod-level hardening

| Key | Type | Default |
|---|---|---|
| `security.podSecurityContext.runAsNonRoot` | bool | `true` |
| `security.podSecurityContext.runAsUser` | int | `1000` |
| `security.podSecurityContext.fsGroup` | int | `1000` |
| `security.containerSecurityContext.readOnlyRootFilesystem` | bool | `true` |
| `security.containerSecurityContext.allowPrivilegeEscalation` | bool | `false` |
| `security.containerSecurityContext.capabilities.drop` | list | `["ALL"]` |
| `security.containerSecurityContext.seccompProfile.type` | string | `RuntimeDefault` |

## networkPolicy — namespace isolation

| Key | Type | Default | Notes |
|---|---|---|---|
| `networkPolicy.enabled` | bool | `true` | 4 NetworkPolicies lock the namespace to its own data plane |
| `networkPolicy.allowExternal.ollamaEgress` | bool | `false` | Set true for first install only (model pull) |

## api — bearer + rate limit + CORS

| Key | Type | Default | Notes |
|---|---|---|---|
| `api.replicas` | int | `1` | HA is post-1.0; PDB `minAvailable=0` makes single-replica explicit |
| `api.resources.limits.memory` | string | `"1Gi"` | |
| `api.allowedOrigins` | list | `[]` (localhost) | CORS — set to your operator-facing domain |
| `api.rateLimit.global` | string | `"120/minute"` | `limits`-library syntax; probes + `/metrics` exempt |
| `api.probes.startup.failureThreshold` | int | `60` | Slow first boot |
| `api.probes.startup.periodSeconds` | int | `5` | |

## mcp — MCP server

| Key | Type | Default |
|---|---|---|
| `mcp.replicas` | int | `1` |
| `mcp.resources.limits.memory` | string | `"512Mi"` |

## frontend — React SPA

| Key | Type | Default | Notes |
|---|---|---|---|
| `frontend.replicas` | int | `2` | Stateless; HA fine |
| `frontend.resources.limits.memory` | string | `"128Mi"` | Pure static nginx |
| `frontend.locale.default` | string | `"en"` | `en` \| `fr` |

## ingress — Caddy + HTTPS

| Key | Type | Default | Notes |
|---|---|---|---|
| `ingress.enabled` | bool | `false` | |
| `ingress.className` | string | `""` | Match your IngressClass |
| `ingress.host` | string | `"keos.local"` | |
| `ingress.tls.enabled` | bool | `false` | |
| `ingress.tls.secretName` | string | `""` | cert-manager or hand-crafted |

## ollama — LLM container

| Key | Type | Default | Notes |
|---|---|---|---|
| `ollama.chatModel` | string | `"qwen2.5:3b-instruct-q4_K_M"` | Pre-pulled in init container |
| `ollama.embedModel` | string | `"nomic-embed-text"` | |
| `ollama.persistence.size` | string | `"10Gi"` | |
| `ollama.resources.limits.memory` | string | `"4Gi"` | |
| `ollama.gpu.enabled` | bool | `false` | NVIDIA only |
| `ollama.gpu.resourceName` | string | `"nvidia.com/gpu"` | |
| `ollama.gpu.count` | int | `1` | |
| `ollama.gpu.runtimeClassName` | string | `""` | `"nvidia"` when enabled |
| `ollama.keepAlive` | string | `"30m"` | Model resident time |

## qdrant — vector DB

| Key | Type | Default |
|---|---|---|
| `qdrant.persistence.size` | string | `"2Gi"` |
| `qdrant.resources.limits.memory` | string | `"512Mi"` |

## nats — JetStream

| Key | Type | Default |
|---|---|---|
| `nats.persistence.size` | string | `"5Gi"` |
| `nats.jetstream.maxAge` | duration | `"24h"` |

## opa — policy engine

| Key | Type | Default | Notes |
|---|---|---|---|
| `opa.resources.limits.memory` | string | `"128Mi"` | OPA is light |
| `opa.policyConfigMap` | string | `"pocteo-pocteo-opa"` | ConfigMap holding `agent.rego` |

## k8sgpt — diagnostic enricher

| Key | Type | Default | Notes |
|---|---|---|---|
| `k8sgpt.enabled` | bool | `true` | Disable for air-gapped CI |
| `k8sgpt.resources.limits.memory` | string | `"256Mi"` | |

## monitoring — Prometheus Operator

| Key | Type | Default | Notes |
|---|---|---|---|
| `monitoring.enabled` | bool | `false` | Creates ServiceMonitor when true |
| `monitoring.scrapeInterval` | duration | `"30s"` | |
| `monitoring.releaseLabel` | string | `"prometheus"` | Match your Operator's `release` label |

## logging

| Key | Type | Default |
|---|---|---|
| `logging.format` | string | `"json"` |
| `logging.level` | string | `"INFO"` |

## retention — data lifecycle

| Key | Type | Default |
|---|---|---|
| `retention.auditDays` | int | `30` |
| `retention.incidentDays` | int | `90` |
| `retention.traceDays` | int | `30` |

## backup — Velero integration

| Key | Type | Default | Notes |
|---|---|---|---|
| `backup.velero.enabled` | bool | `false` | |
| `backup.velero.schedule` | cron | `"0 2 * * *"` | Daily at 02:00 UTC |
| `backup.velero.ttl` | duration | `"720h"` | 30-day side retention |

## inbound — Alertmanager webhook

| Key | Type | Default | Notes |
|---|---|---|---|
| `inbound.alertmanagerWebhookToken` | string | `""` | Unset = 503 |

## notifications — outbound page_human

| Key | Type | Default |
|---|---|---|
| `notifications.slackWebhookUrl` | string | `""` |
| `notifications.genericWebhookUrl` | string | `""` |

## chaos-mesh — sub-chart

| Key | Type | Default | Notes |
|---|---|---|---|
| `chaos-mesh.enabled` | bool | `true` | Disable for air-gapped CI |
| `chaos-mesh.chaosDaemon.runtime` | string | `"containerd"` | |
| `chaos-mesh.chaosDaemon.socketPath` | string | `"/run/k3s/containerd/containerd.sock"` | Talos uses `/run/containerd/containerd.sock` |

See [Environment variables](environment-variables) for the agent process's runtime configuration (separate from the chart values above).
