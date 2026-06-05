# Airgapped install — Kairos on Jetson, no internet

> **Scenario.** A Jetson Orin NX running Kairos in a site with no
> internet access. Operator has a registry mirror reachable from the
> Jetson + a Linux laptop they used to build the airgap bundle on the
> connected side.
>
> **Why this doc exists.** Every install step in the canonical
> [`KAIROS_JETSON.md`](KAIROS_JETSON.md) assumes internet — model
> pulls, NVIDIA toolkit apt repo, k8s device-plugin from GitHub raw,
> the public Helm chart. This doc closes each of those gaps.

---

## What needs to be in your registry mirror

The chart references 12 OCI images. Every one of them must be
present in your mirror at the path the chart expects after the
airgap overrides:

| Source | Destination in your mirror |
|---|---|
| `ghcr.io/pocteo-platform/keos-api:1.0.0` | `<mirror>/pocteo-platform/keos-api:1.0.0` |
| `ghcr.io/pocteo-platform/keos-mcp-server:1.0.0` | `<mirror>/pocteo-platform/keos-mcp-server:1.0.0` |
| `ghcr.io/pocteo-platform/keos-frontend:1.0.0` | `<mirror>/pocteo-platform/keos-frontend:1.0.0` |
| `caddy:2.8-alpine` | `<mirror>/library/caddy:2.8-alpine` |
| `nats:2.10.22-alpine` | `<mirror>/library/nats:2.10.22-alpine` |
| `ollama/ollama:0.4.7` | `<mirror>/ollama/ollama:0.4.7` |
| `qdrant/qdrant:v1.12.4` | `<mirror>/qdrant/qdrant:v1.12.4` |
| `openpolicyagent/opa:0.69.0` | `<mirror>/openpolicyagent/opa:0.69.0` |
| `ghcr.io/chaos-mesh/chaos-mesh:v2.7.2` | `<mirror>/chaos-mesh/chaos-mesh:v2.7.2` |
| `ghcr.io/chaos-mesh/chaos-daemon:v2.7.2` | `<mirror>/chaos-mesh/chaos-daemon:v2.7.2` |
| `ghcr.io/chaos-mesh/chaos-coredns:v0.2.6` | `<mirror>/chaos-mesh/chaos-coredns:v0.2.6` |
| `alpine/k8s:1.30.0` | `<mirror>/alpine/k8s:1.30.0` |

This is what `scripts/build-airgap-bundle.sh` packages + what
`scripts/load-airgap-images.sh` pushes when you run it against
your mirror.

---

## Phase 1 — On a connected build host (Linux, 5–10 min)

```bash
git clone https://github.com/pocteo-platform/keos
cd keos/sre-agent

# This builds the 3 first-party images, pulls the 9 stock ones,
# pre-pulls the Ollama models, packages the chart, and writes
# everything to a single ~5.5 GB tarball.
bash scripts/build-airgap-bundle.sh 1.0.0
# → output: /tmp/keos-airgap-v1.0.0.tar
```

Move the tarball to the airgapped network via your usual sneakernet
(USB stick / approved transfer).

---

## Phase 2 — Pre-bake the Kairos image (one-time)

The Kairos image bundled with the upstream Jetson Orin NX defaults
doesn't include the NVIDIA Container Toolkit. The canonical
`defaults.yaml` from `KAIROS_JETSON.md` installs it at first boot via
`apt-get` — which requires internet.

For airgap you need to **bake the NVIDIA Container Toolkit into the
Kairos image** so first boot doesn't try to reach `nvidia.github.io`.

Two paths:

### Option A — Kairos OCI bundle (recommended)

Build a Kairos derivative image that includes the toolkit as an
overlay. Example Dockerfile fragment:

```dockerfile
FROM quay.io/kairos/ubuntu:22.04-core-arm64-nvidia-jetson-orin-nx-v4.1.0
RUN apt-get update && \
    curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
        | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg && \
    curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
        | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
        > /etc/apt/sources.list.d/nvidia-container-toolkit.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends nvidia-container-toolkit && \
    apt-get clean && rm -rf /var/lib/apt/lists/*
```

Build it on the connected side, push to your registry, then point
auroraboot at it via `IMAGE=<mirror>/kairos-jetson-orin-nx-nvidia:v4.1.0`.

The flashed Jetson then has the toolkit pre-installed; `defaults.yaml`'s
`stages.boot` block can drop the apt-get steps entirely.

### Option B — Mirror NVIDIA's apt repo

If you have an apt-mirror infrastructure already, mirror
`https://nvidia.github.io/libnvidia-container/stable/deb/` and point
`/etc/apt/sources.list.d/nvidia-container-toolkit.list` at your
mirror in the Kairos image build. Same outcome, slightly more
infrastructure to maintain.

---

## Phase 3 — Airgapped `defaults.yaml` for the Jetson

Replace the NVIDIA-toolkit install block in the canonical
`defaults.yaml` with the registry-mirror config + the device-plugin
pre-cached path:

```yaml
#cloud-config
hostname: keos-jetson-{{ trunc 4 .MachineID }}

users:
  - name: kairos
    ssh_authorized_keys:
      - <YOUR_SSH_PUBLIC_KEY>

install:
  device: /dev/nvme0n1
  reboot: true
  auto: true

p2p:
  network_token: <YOUR_BASE64_EDGEVPN_TOKEN>
  role: master
  disable_dht: true
  auto: { enable: false }

# ─── AIRGAP: tell containerd to use your local registry mirror ───────
write_files:
  - path: /etc/rancher/k3s/registries.yaml
    permissions: "0600"
    content: |
      mirrors:
        docker.io:
          endpoint:
            - http://<MIRROR_HOST>:5000
        ghcr.io:
          endpoint:
            - http://<MIRROR_HOST>:5000

stages:
  boot:
    # NVIDIA toolkit is now baked into the Kairos image (Phase 2). We
    # just need to register the RuntimeClass + device plugin, both
    # from the local registry.
    - name: Register the nvidia RuntimeClass
      commands:
        - |
          for i in 1 2 3 4 5 6; do
            kubectl apply -f - <<EOF && break || sleep 10
          apiVersion: node.k8s.io/v1
          kind: RuntimeClass
          metadata: { name: nvidia }
          handler: nvidia
          EOF
          done

    - name: Install NVIDIA k8s device plugin (advertises nvidia.com/gpu)
      commands:
        - |
          # Use a manifest that's been pre-staged into /oem/airgap/.
          # The build-airgap-bundle.sh script puts it there.
          for i in 1 2 3 4 5 6; do
            kubectl apply -f /oem/airgap/nvidia-device-plugin.yaml \
              && break || sleep 10
          done

k3s:
  args:
    - --node-label=keos.pocteo/sre-home=true
    - --tls-san=0.0.0.0
```

Two changes vs the public-internet `defaults.yaml`:
1. **`write_files: /etc/rancher/k3s/registries.yaml`** — k3s reads
   this and routes every `docker.io/*` + `ghcr.io/*` pull through
   your mirror.
2. **`stages.boot` drops the apt-get block** — toolkit is already in
   the image. Just register the RuntimeClass + device plugin, both
   from a manifest staged at `/oem/airgap/nvidia-device-plugin.yaml`
   during the Kairos image build (auroraboot's `IMAGE` build process
   accepts arbitrary `/oem/` writes via cloud-config).

---

## Phase 4 — On the airgapped Jetson side (no internet, ~5 min)

After flashing the Kairos image + booting:

```bash
# From your operator workstation, with KUBECONFIG pointed at the Jetson.
export KUBECONFIG=~/.kube/keos-jetson.yaml

# Extract the bundle (use your sneakernet result from Phase 1).
tar xf keos-airgap-v1.0.0.tar
cd keos-airgap-v1.0.0

# Push every image into the mirror (replace MIRROR with yours).
bash scripts/load-airgap-images.sh <MIRROR_HOST>:5000

# Install with --airgap (refuses to reach the public chart repo).
bash scripts/install-edge.sh --airgap \
  --local-chart ./chart/keos-sre-agent-1.0.0.tgz \
  --image-registry <MIRROR_HOST>:5000

# Restore the pre-pulled Ollama models so the first diagnose call
# doesn't try to ollama.ai.
bash scripts/seed-ollama-models.sh
```

`install-edge.sh --airgap` does three extra things vs the normal mode:
1. **Refuses to run without `--local-chart` + `--image-registry`**.
2. **Skips `helm repo add` + `helm repo update`** — no public Helm repo.
3. **Sets every per-image `repository`** to the mirror path so kubelet
   pulls every container from your mirror, not from `docker.io` or
   `ghcr.io`.

---

## Verifying

```bash
# Every component green
curl -H "Authorization: Bearer $(cat ~/.keos-token)" \
     http://<jetson-ip>:30080/api/health/detailed | jq

# Cluster context probe is intact
curl -H "Authorization: Bearer $(cat ~/.keos-token)" \
     http://<jetson-ip>:30080/api/cluster/context | jq

# GPU inference is working
kubectl -n keos exec sts/ollama -- ollama ps
#   PROCESSOR column should read "100% GPU"
```

---

## Total bundle size

| Component | Size |
|---|---|
| All 12 OCI images (`docker save`) | ~3.0 GB |
| Helm chart + Chaos Mesh sub-chart | ~150 KB |
| Pre-pulled Ollama models | ~2.2 GB |
| Install scripts + README | ~30 KB |
| **Total** | **~5.5 GB** |

Fits comfortably on a 16 GB USB stick alongside the Kairos OS image.
For sites with bandwidth at all (cellular fallback, satellite, etc.),
the install side is purely CPU/IO-bound — no network roundtrips
needed after the bundle's on the airgapped side.

---

## What does NOT need internet

- ✅ The chart install itself (uses local `.tgz`)
- ✅ Every container pull (all via mirror)
- ✅ The Ollama model load (pre-cached blobs)
- ✅ The runbook RAG seed (uses local Ollama + Qdrant)
- ✅ The agent's cluster-context probe (in-cluster K8s API only)
- ✅ All chart sub-chart deps (`chaos-mesh-2.7.2.tgz` vendored under `charts/`)
- ✅ All Python deps in the API image (pre-installed via `uv` at build time)
- ✅ The NVIDIA Container Toolkit (baked into Kairos image in Phase 2)
- ✅ The NVIDIA k8s device plugin (manifest pre-staged in `/oem/airgap/`)

## What DOES need a one-time connected hop

- 🔌 Building the airgap bundle (`scripts/build-airgap-bundle.sh`)
- 🔌 Building the Kairos+NVIDIA derivative image (Phase 2)
- 🔌 Initial L4T SDK download from NVIDIA developer site

These happen on the operator's build workstation, not on the
airgapped Jetson side.
