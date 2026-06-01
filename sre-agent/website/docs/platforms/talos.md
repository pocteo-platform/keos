---
id: talos
slug: /platforms/talos
title: Talos Linux
sidebar_position: 6
---

# Keos on Talos Linux

Talos is a minimal, API-driven Linux for Kubernetes — no shell, no SSH on
the nodes, machine config delivered via `talosctl`. Keos deploys
cleanly onto Talos because every component is a containerised workload
with no kernel dependencies (no eBPF, no Falco).

## What's different on Talos

| Aspect | Other distros | Talos |
|---|---|---|
| Container runtime | depends | containerd, always |
| containerd socket | varies (`/run/k3s/containerd/...` on k3s, `/var/run/docker.sock` on Docker) | `/run/containerd/containerd.sock` |
| Host access | SSH | `talosctl` API only |
| Persistent storage | local-path / openebs / longhorn / cloud CSI | same — pick any conformant CSI |
| Image pre-pull | docker pull / k3s ctr | `talosctl image cache` |

## Install Keos on Talos

```bash
helm install pocteo deploy/helm/pocteo \
    --namespace pocteo --create-namespace \
    --set chaos-mesh.chaosDaemon.runtime=containerd \
    --set chaos-mesh.chaosDaemon.socketPath=/run/containerd/containerd.sock \
    --set-string auth.apiToken=$(openssl rand -base64 32) \
    --set-string auth.hmacKey=$(openssl rand -base64 32) \
    --wait --timeout 5m
```

The two `chaos-mesh.chaosDaemon.*` overrides are the only Talos-specific
flags. Everything else (RBAC, CRDs, ConfigMaps, StatefulSets) works
identically.

## Persistent storage on Talos

Keos's StatefulSets (Ollama, Qdrant, NATS) need a working
StorageClass. Talos doesn't ship one by default. Options:

- **Single-node / dev**: install Rancher's local-path-provisioner:
  ```bash
  kubectl apply -f https://raw.githubusercontent.com/rancher/local-path-provisioner/master/deploy/local-path-storage.yaml
  kubectl patch storageclass local-path -p '{"metadata": {"annotations":{"storageclass.kubernetes.io/is-default-class":"true"}}}'
  ```

- **Multi-node**: install OpenEBS or Longhorn. Set the class explicitly
  in `values.yaml`:
  ```yaml
  ollama:
    persistence:
      storageClass: openebs-hostpath
  qdrant:
    persistence:
      storageClass: openebs-hostpath
  nats:
    persistence:
      storageClass: openebs-hostpath
  ```

## Air-gapped install on Talos

Pre-cache every image Keos needs into the node's containerd image
store. Run once per node:

```bash
talosctl image cache --talosconfig ./talosconfig \
    ghcr.io/your-org/pocteo-api:0.1.0 \
    ghcr.io/your-org/pocteo-mcp-server:0.1.0 \
    ghcr.io/your-org/pocteo-frontend:0.1.0 \
    ollama/ollama:0.4.7 \
    qdrant/qdrant:v1.12.4 \
    nats:2.10.22-alpine \
    openpolicyagent/opa:0.69.0 \
    caddy:2.8-alpine \
    ghcr.io/chaos-mesh/chaos-mesh:v2.7.2 \
    ghcr.io/chaos-mesh/chaos-daemon:v2.7.2
```

Then run `helm install` as above — the pulls will hit the local cache.

## Reference machine config

See `deploy/talos/talconfig.example.yaml`. Apply per node:

```bash
talosctl --nodes <ip> apply-config --file talconfig.yaml
```

## Troubleshooting on Talos

| Symptom | Check |
|---|---|
| Chaos Mesh pods CrashLoop on chaos-daemon | Wrong socket — verify `/run/containerd/containerd.sock` exists in the node's containerd config |
| PVCs stuck `Pending` | No default StorageClass — see persistent-storage section above |
| Image pull errors | Air-gapped install missing — pre-cache via `talosctl image cache` |
| Can't access `kubelet logs` | Talos has no shell. Use `talosctl logs <component>` or `talosctl dmesg` instead |

## What you cannot do on Talos (and how Keos handles it)

- **No SSH to the node**: Keos never needs SSH. The agent runs as a
  Pod and uses the in-cluster ServiceAccount.
- **No host bind mounts**: The Helm chart uses PVCs everywhere. The
  laptop `docker-compose` bind mounts don't apply.
- **No `kubectl exec ... bash` on nodes**: Keos's runtime debug uses
  the K8s API + logs only.

Keos was designed with Talos in mind from Phase 9.5. If you hit a
Talos-specific issue not in this doc, please file an issue with the
output of `talosctl health`.
