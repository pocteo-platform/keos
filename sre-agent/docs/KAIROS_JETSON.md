# Kairos on Jetson Orin NX — operator install guide

> **What this doc is.** The canonical, no-headaches install recipe
> for the production hardware target: an **NVIDIA Jetson Orin NX**
> running **Kairos** ([kairos.io](https://kairos.io)) as the
> immutable Kubernetes OS, with **GPU inference** wired up and
> **EdgeVPN peer-to-peer** ready for adding more edges later.
>
> **What this doc is NOT.** A theoretical platform reference. Every
> command here is what the operator types in order. If a step needs
> a value (token, SSH key, hostname), there's a placeholder you fill
> in before running it.
>
> **Total time:** ~60 min the first time (most of it is flashing
> + ~25 min Ollama model download on the device). Subsequent
> Jetsons take ~20 min if you reuse the same Kairos image.

---

## Why Kairos (vs Talos vs anything else)

| Concern | Kairos answer |
|---|---|
| Immutable OS, atomic upgrades | ✓ (recovery + active partitions, rollback on failure) |
| Kubernetes bundled | ✓ (k3s default; k0s + RKE2 available) |
| GPU on Jetson | ✓ (NVIDIA Container Toolkit via cloud-init / bundle) |
| **Peer-to-peer mesh between edges** | **✓ EdgeVPN — the killer feature** |
| SSH access for debugging | ✓ (cloud-init provisions operator keys) |
| Air-gapped / OTA upgrades | ✓ |
| Documented for Jetson Orin NX | ✓ ([upstream link](https://kairos.io/docs/installation/nvidia_orin_nx/)) |

The P2P piece is why we chose Kairos. When you add a second Jetson
in a different building, you don't run cables — drop the same
`p2p.network_token` into its cloud-config and the two edges form
an encrypted libp2p mesh + k3s sees them as cluster peers.

---

## 0. Hardware checklist

| Item | Notes |
|---|---|
| **Jetson Orin NX** dev kit (or carrier board) | Recommended: NX **16 GB** for headroom on `qwen2.5:3b` + workloads. NX 8 GB works but tight. |
| **NVMe SSD** in the M.2 Key M slot | ≥ 64 GB. eMMC is too small for the Ollama model cache (~3 GB) + persistent data. Kairos partitions sizes assume NVMe. |
| **USB Type-C cable** | For flashing. **Not** the same port as serial — Orin has a Type-C for data + a Micro-USB for serial debug. |
| **Micro-USB cable** (optional) | Serial console — useful if first boot fails and you can't reach over network. |
| **Linux host machine** | Required for flashing. NVIDIA's L4T SDK + `flash.sh` don't run on Windows or macOS. WSL2 with `usbipd` works but is fiddly — borrow a Linux laptop for the flash step if you can. |
| Ethernet cable | Plug the Jetson into the same network as your workstation. |
| **Screen + keyboard** | First-boot sanity-check only. Once SSH works you're done with these. |

---

## 1. On the Linux host — prepare the Kairos image (~15 min)

These steps run on **your Linux host workstation**, not on the Jetson.
At the end you'll have flashable partition images + a cloud-config
embedded in them.

### 1.1 Install host prerequisites

```bash
sudo apt-get install -y wget tar docker.io
sudo usermod -aG docker $USER && newgrp docker
```

You need Docker because Kairos's `auroraboot` runs as a container.

### 1.2 Download NVIDIA's Jetson Linux SDK (L4T 36.4.4 / JetPack 6.2.1)

```bash
mkdir -p ~/kairos-jetson && cd ~/kairos-jetson

wget https://developer.nvidia.com/downloads/embedded/l4t/r36_release_v4.4/release/Jetson_Linux_r36.4.4_aarch64.tbz2 \
     -O tegra.tbz2
tar xjf tegra.tbz2
cd Linux_for_Tegra
```

### 1.3 Disable Extlinux (Kairos uses GRUB)

```bash
echo "" > ./bootloader/extlinux.conf
```

### 1.4 Write your cloud-config — `defaults.yaml`

This is the **one file you customize**. It's embedded into the image
at flash time, and it's what makes first boot Just Work.

Save the following as `~/kairos-jetson/defaults.yaml`. **Fill in the
three `<PLACEHOLDER>` values** before continuing.

```yaml
#cloud-config

# ─── Operator identity ─────────────────────────────────────────────
hostname: keos-jetson-{{ trunc 4 .MachineID }}

users:
  - name: kairos
    # Change this password OR drop it entirely and rely on the SSH key.
    passwd: <CHANGE_ME_OR_DELETE_THIS_LINE>
    groups: [admin]
    ssh_authorized_keys:
      # Paste the contents of `cat ~/.ssh/id_ed25519.pub` from your
      # workstation here. Without this, you have no way to SSH in.
      - <YOUR_SSH_PUBLIC_KEY>

# ─── Install target ────────────────────────────────────────────────
install:
  device: /dev/nvme0n1     # NVMe SSD — not the eMMC.
  reboot: true
  auto: true

# ─── EdgeVPN peer-to-peer mesh ─────────────────────────────────────
# Generate the token once on your laptop:
#   docker run --rm -ti quay.io/mudler/edgevpn -b -g
# Then paste the base64 output here. SAME TOKEN on every Jetson you
# want in the same mesh. Different network = different token.
p2p:
  network_token: <YOUR_BASE64_EDGEVPN_TOKEN>
  # Single-node clusters: this is the only/master node.
  role: master
  # Force-disable DHT — local-network discovery only. Flip to false
  # if you need cross-internet peering (NAT traversal kicks in then).
  disable_dht: true
  # Skip the role-auto-election so 'master' above is honoured.
  auto:
    enable: false

# ─── NVIDIA GPU runtime (containerd nvidia handler) ────────────────
# Kairos doesn't ship the NVIDIA toolkit on the Orin image — we add
# it via a boot-stage hook so kubelet sees nvidia.com/gpu on first
# Ready. Skip this block if you intend to run CPU-only.
stages:
  boot:
    - name: Install NVIDIA Container Toolkit
      commands:
        - |
          set -e
          curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
          curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
            | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
            > /etc/apt/sources.list.d/nvidia-container-toolkit.list
          apt-get update -qq
          apt-get install -y nvidia-container-toolkit
          nvidia-ctk runtime configure --runtime=containerd --config=/var/lib/rancher/k3s/agent/etc/containerd/config.toml.tmpl
          systemctl restart k3s

    - name: Register the nvidia RuntimeClass
      commands:
        - |
          # Retry until kubectl works (k3s may still be starting).
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
          for i in 1 2 3 4 5 6; do
            kubectl apply -f https://raw.githubusercontent.com/NVIDIA/k8s-device-plugin/v0.16.2/deployments/static/nvidia-device-plugin.yml \
              && break || sleep 10
          done

# ─── k3s tuning for the Keos workload ──────────────────────────────
k3s:
  args:
    # Single-node Kairos clusters need scheduling on control-plane.
    - --node-label=keos.pocteo/sre-home=true
    # Talos-style: kube-apiserver bound to all addresses for SSH-fetchable kubeconfig.
    - --tls-san=0.0.0.0
```

### 1.5 Generate the network_token

```bash
docker run --rm -ti quay.io/mudler/edgevpn -b -g
# → prints a base64 blob. Copy it into `defaults.yaml` above
#   as the value of p2p.network_token. Same token = same mesh.
#   Lose this token = lose the mesh; treat it like a password.
```

### 1.6 Unpack the Kairos rootfs + generate partition images

```bash
cd ~/kairos-jetson/Linux_for_Tegra
IMAGE=quay.io/kairos/ubuntu:22.04-core-arm64-nvidia-jetson-orin-nx-v4.1.0

# Unpack the Kairos image into ./rootfs/
docker run -ti --rm -v $PWD/rootfs:/rootfs \
  quay.io/luet/base util unpack "$IMAGE" /rootfs

# Kairos uses GRUB, not extlinux.
rm rootfs/boot/initrd
mkdir -p rootfs/boot/extlinux/
echo "" > rootfs/boot/extlinux/extlinux.conf

# Build the partition images, with our cloud-config embedded.
docker run --privileged --platform=linux/arm64 \
  -e container_image=$IMAGE \
  -e STATE_SIZE="25500" \
  -e RECOVERY_SIZE="21000" \
  -e DEFAULT_ACTIVE_SIZE="7000" \
  -v $PWD/bootloader:/bootloader \
  -v ~/kairos-jetson/defaults.yaml:/defaults.yaml \
  --entrypoint /prepare_nvidia_orin_images.sh \
  -ti --rm quay.io/kairos/auroraboot:v0.20.1
```

This produces under `bootloader/`:
- `efi.img`
- `recovery_partition.img`
- `state_partition.img`
- `oem.img`
- `persistent.img`

### 1.7 Apply the partition layout

```bash
wget 'https://kairos.io/examples/board-configs/flash_t234_qspi_nvme.xml' \
     -O ./bootloader/generic/cfg/flash_t234_qspi_nvme.xml

# Sanity-check that the image sizes match the layout's <size> tags.
# If they don't, edit flash_t234_qspi_nvme.xml in place.
for f in efi recovery_partition state_partition oem persistent; do
  printf "  %-22s  %s bytes\n" "$f.img" "$(stat -c %s bootloader/$f.img)"
done
```

---

## 2. Flash the Jetson (~10 min)

### 2.1 Put the Orin NX into recovery mode

1. Power off the board.
2. Hold the **REC** button.
3. Plug the **USB Type-C** cable from the Orin's data port into your
   Linux host.
4. Power on.
5. Release REC after ~3 seconds.

Verify the host sees it:

```bash
lsusb | grep -i nvidia
# Expect: NVIDIA Corp. APX  (or similar — the key word is "APX")
```

### 2.2 Run NVIDIA's flash prerequisites once

```bash
cd ~/kairos-jetson/Linux_for_Tegra
sudo ./tools/l4t_flash_prerequisites.sh
```

### 2.3 Generate the ESP (one-time per host)

```bash
BOARD_CONFIG=brla4n-orin-nx     # the standard Orin NX dev-kit board ID
sudo ./flash.sh --no-flash --no-systemimg $BOARD_CONFIG mmcblk0p1
```

### 2.4 The actual flash

```bash
sudo ./tools/kernel_flash/l4t_initrd_flash.sh \
  --external-device nvme0n1p1 \
  -c tools/kernel_flash/flash_l4t_t234_nvme_kairos.xml \
  -p "-c bootloader/t186ref/cfg/flash_t234_qspi.xml" \
  --network usb0 $BOARD_CONFIG internal
```

Takes ~8 min. The board auto-reboots when done. Disconnect USB-C.

---

## 3. First boot on the Jetson (~2 min)

Plug Ethernet in. The board boots, picks up DHCP, runs cloud-init.
The `defaults.yaml` you embedded does:

1. Provisions user `kairos` with your SSH key
2. Installs the NVIDIA Container Toolkit + `nvidia` RuntimeClass +
   device plugin
3. Starts k3s (single-node server)
4. Joins the EdgeVPN mesh with your network_token
5. Labels itself `keos.pocteo/sre-home=true` so the agent's hard-pin works

Find the Jetson's IP from your router's DHCP page, then from your
workstation:

```bash
# Should connect using your SSH key — no password.
ssh kairos@<jetson-ip>

# Once in:
kubectl get nodes
# NAME                STATUS   ROLES                  AGE    VERSION
# keos-jetson-aB3f    Ready    control-plane,master   2m     v1.30.x+k3s1

kubectl get runtimeclass
# NAME     HANDLER   AGE
# nvidia   nvidia    1m

kubectl describe node | grep nvidia.com/gpu
#   nvidia.com/gpu: 1   (Capacity + Allocatable)
exit
```

If all three checks pass, the Jetson is fully provisioned. Don't
SSH in again unless you're debugging — everything else happens from
your workstation via `kubectl`.

---

## 4. From your workstation — install Keos (~25 min)

This is where you stop being on Linux and switch back to wherever
your daily driver is (Mac / Windows / Linux laptop). You only need
`kubectl` + `helm` + the Keos install script.

### 4.1 Fetch the Jetson's kubeconfig

```bash
ssh kairos@<jetson-ip> 'sudo cat /etc/rancher/k3s/k3s.yaml' \
  | sed "s/127.0.0.1/<jetson-ip>/" \
  > ~/.kube/keos-jetson.yaml

export KUBECONFIG=~/.kube/keos-jetson.yaml
kubectl get nodes      # → 1 node Ready
```

### 4.2 Run the installer

```bash
git clone https://github.com/pocteo-platform/keos
cd keos/sre-agent

bash scripts/install-edge.sh
```

The installer auto-detects:
- **Distro:** `kairos` (from the node's `osImage` field)
- **Bundled K8s:** k3s → uses `/run/k3s/containerd/containerd.sock`
  for Chaos Mesh
- **GPU:** detects `nvidia.com/gpu=1` → enables Ollama GPU offload
  with `runtimeClassName: nvidia`

What it does next:
1. Generates a bearer token + audit HMAC key (saved to `~/.keos-token`)
2. `helm install` with the chart's Talos/Kairos defaults
3. Pulls `qwen2.5:3b-instruct-q4_K_M` (~1.9 GB, ~5 min on a fast link)
   + `nomic-embed-text` (~270 MB, ~30 s) inside the Ollama pod
4. Seeds the 12-runbook corpus into Qdrant via the api pod
5. Prints the URL + token

After install completes (~25 min cold), the printout looks like:

```
==================================================================
  Keos SRE Agent is up on kairos.
==================================================================

  Open in your browser:
    http://<jetson-ip>:30080

  Bearer token (paste it on the login screen):
    <random-32-char-string>

  Helm release:  keos · namespace: keos
  Pinned to:     keos-jetson-aB3f (label keos.pocteo/sre-home=true)
  Agent mode:    observe-only (flip to 'active' from Settings → Agent)
```

### 4.3 Verify GPU inference is working

```bash
# From the workstation, against the Jetson's k3s.
kubectl -n keos exec -it sts/ollama -- ollama ps
# Expect: PROCESSOR column reads "100% GPU"
#         (not "100% CPU" — that means the runtime class didn't bind)

# Also confirm directly:
kubectl -n keos exec sts/ollama -- nvidia-smi
# Expect: a Tegra Orin entry with the qwen2.5 process consuming VRAM.
```

Inference speed comparison on this hardware:
- CPU-only fallback: ~60-90 s per `diagnose` LLM call
- GPU offload on Orin NX 16 GB: **~5-10 s per call** (10× speedup)

---

## 5. Adding a second Jetson to the mesh (~30 min)

This is the EdgeVPN payoff. Different physical site, possibly behind
NAT — same mesh, same cluster, P2P.

1. Repeat steps 1–3 with the **same `defaults.yaml`** (same
   `network_token`, same SSH key). Change one line:
   ```yaml
   p2p:
     role: worker        # ← was 'master' on the first
   ```
2. Boot the second Jetson, plug it into ethernet anywhere.
3. From the workstation, check the mesh status via the first Jetson:
   ```bash
   ssh kairos@<first-jetson-ip> kairosctl bridge --network-token <TOKEN>
   # Opens a local API at http://127.0.0.1:8080 with peer list.
   ```
4. The new node auto-registers with k3s (EdgeVPN tunnel + k3s
   join token are both bootstrapped from the network_token).
5. Re-run the Keos installer's Helm upgrade to allow the second node
   for workload scheduling, OR keep the agent pinned hard via the
   `keos.pocteo/sre-home` label (the second Jetson runs cluster
   workloads but not the agent).

---

## 6. Day-2 operations

### Where things live
- **Operator's workstation:** SPA at `http://<jetson-ip>:30080`,
  `~/.kube/keos-jetson.yaml`, `~/.keos-token`.
- **Jetson (read-only OS):** Kairos rootfs is immutable. Only writable
  paths: `/var/lib/rancher/` (k3s state) + `/oem/` + `/usr/local/.state/`.
- **State that survives reboot:** persistent partition (`/usr/local/.state`),
  k3s database, EdgeVPN state.
- **State that doesn't:** anything in `/tmp`, `/var/run`, pod ephemeral
  storage.

### Upgrading Kairos
```bash
ssh kairos@<jetson-ip> sudo kairos-agent upgrade --image quay.io/kairos/ubuntu:22.04-core-arm64-nvidia-jetson-orin-nx-vX.Y.Z
sudo reboot
```
Atomic — boots into the new image, rolls back automatically if k3s
doesn't come up within the boot-assessment window.

### Upgrading Keos
```bash
cd keos/sre-agent
git pull
helm upgrade keos deploy/helm/pocteo \
  --kube-context admin@<context> \
  --namespace keos --reuse-values
```

### Uninstall
```bash
helm uninstall keos -n keos
kubectl delete namespace keos
```

### Factory reset (start over without re-flashing)
```bash
ssh kairos@<jetson-ip> sudo kairos-agent reset
sudo reboot
```
Wipes `/usr/local/.state` + re-runs cloud-init from `defaults.yaml`.

---

## 7. Air-gapped install

Skip the public image pulls during step 4.2 by pre-loading them into
a registry the Jetson can reach.

```bash
# On a connected host
docker pull ollama/ollama:0.4.7
docker pull qdrant/qdrant:v1.12.4
docker pull nats:2.10.22-alpine
docker pull openpolicyagent/opa:0.69.0
docker pull caddy:2.8-alpine
docker pull ghcr.io/pocteo-platform/keos-api:1.0.0
docker pull ghcr.io/pocteo-platform/keos-mcp-server:1.0.0
docker pull ghcr.io/pocteo-platform/keos-frontend:1.0.0
# ... save + sneakernet to your registry mirror

# On the Jetson, add a registry mirror via cloud-config:
#   registries.mirrors.docker.io.endpoint = ["http://<mirror>:5000"]
```

Ollama models — pre-populate `/var/lib/rancher/k3s/storage/<pvc>/`
on the Jetson NVMe with a `models/` directory copied from a connected
machine that already ran `ollama pull`.

---

## 8. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `lsusb` doesn't show NVIDIA APX | Board not in recovery mode | Power-cycle while holding REC; try a different USB-C cable |
| Flash fails: "USB timeout" | Known issue on some L4T kernels | Try a different host kernel or USB port (USB3 vs USB2) |
| First-boot stuck at GRUB | extlinux.conf wasn't blanked | Re-do step 1.3, re-flash |
| SSH refused after first boot | DHCP didn't kick in, or your ssh key wasn't in defaults.yaml | Plug screen + USB keyboard, login `kairos`/`kairos`, check `ip a` |
| `kubectl get nodes` says "NotReady" | NVIDIA toolkit didn't install (stages.boot failed) | `journalctl -u k3s -n 50` on the Jetson; usually a network glitch — `kubectl describe node` shows the reason |
| `nvidia.com/gpu` missing from `kubectl describe node` | device plugin DaemonSet didn't start | `kubectl -n kube-system get ds nvidia-device-plugin-daemonset` — check pod logs |
| Ollama pod stuck Pending | Tried to schedule before RuntimeClass landed | Wait ~30 s; the stages.boot retry loop catches this. Or re-create: `kubectl delete pod -n keos ollama-0` |
| `ollama ps` shows 100% CPU instead of GPU | runtimeClassName not applied | `kubectl get sts ollama -n keos -o yaml \| grep runtime`; if missing, `helm upgrade --reuse-values --set ollama.gpu.enabled=true --set ollama.gpu.runtimeClassName=nvidia` |
| `kairosctl bridge` says no peers | Different network_token across Jetsons | Same exact base64 string on both — copy-paste, don't retype |
| Mesh works locally but not across internet | `p2p.disable_dht: true` blocks NAT traversal | Set it to `false` on both nodes |
| EdgeVPN token leaked | Anyone with it joins your mesh | Regenerate, re-flash all nodes with the new token |

---

## 9. References

- Kairos Jetson Orin NX install: <https://kairos.io/docs/installation/nvidia_orin_nx/>
- Kairos P2P / EdgeVPN: <https://kairos.io/docs/installation/p2p/>
- Kairos cloud-config schema: <https://kairos.io/docs/reference/configuration/>
- Kairos single-node example: <https://kairos.io/docs/examples/single-node-p2p/>
- NVIDIA L4T R36.4.4 (JetPack 6.2.1): <https://developer.nvidia.com/embedded/jetson-linux-r3644>
- NVIDIA k8s device plugin: <https://github.com/NVIDIA/k8s-device-plugin>
- Keos cluster-side install reference: [`INSTALL.md`](INSTALL.md)
- Keos UI walkthrough: [`UI_GUIDE.md`](UI_GUIDE.md)

---

## What to expect (timeline summary)

| Phase | Time | Where |
|---|---|---|
| Download L4T SDK + Kairos image | 5-10 min | Linux host |
| Build partition images | ~5 min | Linux host (Docker) |
| Flash Jetson | ~10 min | Linux host + USB-C |
| First boot + cloud-init | ~3 min | Jetson (autonomous) |
| Fetch kubeconfig + ssh check | ~1 min | Workstation |
| `install-edge.sh` install + model pull + seed | ~25 min | Workstation → Jetson |
| **Total** | **~50-60 min** | |

Subsequent identical-config Jetsons (with the same `defaults.yaml`):
~25 min each (model pull dominates).
