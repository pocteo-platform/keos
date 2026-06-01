---
id: kubernetes-helm
slug: /platforms/kubernetes-helm
title: Kubernetes via Helm
sidebar_position: 2
description: Production install path. Helm chart deploys the full stack into any conformant Kubernetes cluster.
---

# Kubernetes via Helm

The Helm chart at `deploy/helm/pocteo/` installs the full Keos stack as
in-cluster workloads — same topology as the laptop demo, but the
agent runs in-cluster with a ServiceAccount instead of from your host.

## Prerequisites

| Tool | Version | Why |
|---|---|---|
| Kubernetes | ≥ 1.27 | Target cluster |
| `kubectl` | ≥ 1.27 | Talk to it |
| `helm` | ≥ 3.13 | Install the chart |
| StorageClass | with `WaitForFirstConsumer` | Stateful sets need PVCs |
| Ingress controller | optional | If you want HTTPS + custom domain |

The agent runs CPU-only by default. For GPU acceleration on Jetson see
[k3s on Jetson](jetson).

## Quick install

```bash
helm install pocteo oci://ghcr.io/pocteo-platform/charts/pocteo \
    --version 1.0.0-rc.1 \
    --namespace pocteo --create-namespace \
    --set-string auth.apiToken="$(openssl rand -base64 32)" \
    --set-string auth.hmacKey="$(openssl rand -base64 32)" \
    --wait --timeout 10m
```

Defaults are sensible. The `--wait` flag blocks until every Deployment
+ StatefulSet is Ready (or it times out at 10 min — increase for slow
LLM model pulls).

## Post-install

Get the bearer token + UI URL:

```bash
kubectl -n pocteo get secret pocteo-pocteo-auth \
  -o jsonpath='{.data.SHP_API_TOKEN}' | base64 -d
kubectl -n pocteo get ingress pocteo-frontend -o jsonpath='{.spec.rules[0].host}'
```

If no ingress is configured, port-forward:

```bash
kubectl -n pocteo port-forward svc/pocteo-caddy 8080:80
# UI now at http://localhost:8080
```

Verify the install:

```bash
kubectl -n pocteo exec deploy/api -- python -m scripts.doctor
```

All checks should be `[OK]`.

## Common values you'll want to override

```yaml
# values-production.yaml
auth:
  # Provide real values; do NOT use the default placeholders.
  apiToken: "<32 bytes of entropy>"
  hmacKey: "<32 bytes of entropy>"

ingress:
  enabled: true
  className: nginx
  host: keos.example.com
  tls:
    enabled: true
    secretName: keos-tls

networkPolicy:
  enabled: true     # locks down the namespace to its own data plane

monitoring:
  enabled: true     # creates a ServiceMonitor for Prometheus Operator
  scrapeInterval: 30s

ollama:
  gpu:
    enabled: true   # see jetson.md for hardware setup
    runtimeClassName: nvidia

backup:
  velero:
    enabled: true   # creates a Velero Schedule CR for nightly backup
    schedule: "0 2 * * *"
```

Install with:

```bash
helm upgrade pocteo oci://ghcr.io/pocteo-platform/charts/pocteo \
    --version 1.0.0-rc.1 \
    --namespace pocteo \
    --values values-production.yaml \
    --wait --timeout 10m
```

See [Helm values](../configuration/helm-values) for the complete
reference of every knob.

## Upgrade

Helm tracks revisions. Bump the chart version and apply:

```bash
helm upgrade pocteo oci://ghcr.io/pocteo-platform/charts/pocteo \
    --version 1.0.0 \
    --namespace pocteo \
    --reuse-values \
    --wait --timeout 10m
```

Before the upgrade, run through the [upgrade runbook](../runbooks/upgrading)
for the pre-upgrade checklist (backup, breaking-change matrix, rollback plan).

## Uninstall

```bash
helm uninstall pocteo --namespace pocteo
kubectl delete namespace pocteo
```

PVCs are retained by default (so audit chains survive). Delete them
explicitly if you want a full wipe:

```bash
kubectl -n pocteo delete pvc --all
kubectl delete namespace pocteo
```

## Production hardening checklist

The chart ships with sensible defaults. Verify these are enabled before
going to production:

- [ ] `auth.apiToken` and `auth.hmacKey` are NOT the placeholder values
- [ ] `networkPolicy.enabled=true`
- [ ] `security.podSecurityContext` honors your cluster's PSS
- [ ] `monitoring.enabled=true` and Prometheus is scraping
- [ ] `backup.velero.enabled=true` and a Schedule exists
- [ ] `ingress.tls.enabled=true` (HTTPS in front of Caddy)
- [ ] `retention.auditDays` matches your compliance requirement

See the [operator hardening checklist in SECURITY.md](https://github.com/pocteo-platform/keos/blob/main/sre-agent/SECURITY.md)
for the long list.
