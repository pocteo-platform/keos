# Jetson Orin Nano — first-boot install walkthrough (Talos / Kairos)

> **Scenario.** A Jetson Orin Nano arrives preconfigured with an
> **immutable Kubernetes OS** — either **Talos**
> ([talos.dev](https://www.talos.dev)) or **Kairos**
> ([kairos.io](https://kairos.io)). It runs a single-node Kubernetes
> cluster.
> You plug it into ethernet + a screen. This doc tells you exactly
> what to do — from your workstation — to install the Keos SRE Agent
> on that box, observe its first cluster incident, and have the
> Jetson healing itself end-to-end.
>
> **Total time:** ~12 min on a warm install (models cached in the OS
> image), ~25 min on a cold install (Ollama pulls
> `qwen2.5:3b-instruct-q4_K_M`, ~1.9 GB).

---

## 0. Why immutable OS changes the install flow

Talos and Kairos are both **immutable** Kubernetes OSes — the root
filesystem is read-only, upgrades are atomic, configuration is
declarative. They differ in a few key ways:

| | Talos | Kairos |
|---|---|---|
| Bundled Kubernetes | k8s direct (kubelet, no shell) | **k3s by default** (also supports k0s, RKE2) |
| SSH access | ✗ — managed via `talosctl` API only | ✓ — SSH keys provisioned via cloud-init |
| Host management | `talosctl` (Talos API) | cloud-init + `kairos-agent` |
| containerd socket | `/run/containerd/containerd.sock` | `/run/k3s/containerd/containerd.sock` (k3s) |
| Single-node K8s | ✓ | ✓ |
| NVIDIA toolkit + `nvidia` RuntimeClass | preconfigured via System Extension | preconfigured via Kairos bundle |
| Argo CD / Flux / Prometheus | not included | not included |

The install pattern is the same for both: **run the installer from
your workstation, not on the Jetson**. The Jetson exposes the
Kubernetes API on port 6443 — that's all the workstation needs. The
installer drives `kubectl` + `helm` against it.

If anything below isn't true, that's a flash-image issue — escalate
to whoever built your OS image; don't try to "fix" the node from
the inside, you can't.

---

## 1. Prepare your workstation (one-time, 2 min)

On your **laptop or jump host**, install:

- `kubectl` ≥ 1.28 — [install](https://kubernetes.io/docs/tasks/tools/)
- `helm` ≥ 3.14 — [install](https://helm.sh/docs/intro/install/)
- `openssl`, `curl`, `python3` (preinstalled on macOS + most Linux)
- For **Talos** only: `talosctl` ≥ 1.7 — [install](https://www.talos.dev/v1.7/talos-guides/install/talosctl/)
- For **Kairos** only: an `ssh` client (preinstalled on macOS + Linux,
  `ssh.exe` on Windows 10+)

That's the entire workstation prerequisite list. No Docker, no node
modules, nothing else.

---

## 2. Connect to the Jetson (one-time, 2 min)

Plug the Jetson into ethernet + a screen. The screen shows the OS's
boot banner and the node's IP — write it down.

### Option A — Talos

Fetch the kubeconfig **from the Jetson's Talos API**:

```bash
# From your workstation. <jetson-ip> is what the screen displayed.
# <talosconfig> is the file your OS-image builder gave you (usually
# named talosconfig.yaml or admin.yaml — contains the operator's PKI).
talosctl --talosconfig <talosconfig> \
         --nodes <jetson-ip>          \
         kubeconfig ~/.kube/keos-jetson.yaml

export KUBECONFIG=~/.kube/keos-jetson.yaml
kubectl get nodes
# NAME            STATUS   ROLES                  AGE   VERSION
# keos-jetson-1   Ready    control-plane,worker   3m    v1.30.4
```

If `kubectl get nodes` works, you're done with step 2.

### Option B — Kairos

Kairos provisions SSH access via cloud-init at first boot (the SSH
keys live in the OS's user-data config — your OS-image builder seeded
the operator's public key). Kairos also bundles **k3s by default**,
so the kubeconfig is at the standard k3s path:

```bash
# From your workstation. Pull the kubeconfig over SSH, rewrite the
# server URL to the Jetson's IP, and point KUBECONFIG at it.
ssh kairos@<jetson-ip> 'sudo cat /etc/rancher/k3s/k3s.yaml' \
  | sed "s/127.0.0.1/<jetson-ip>/" \
  > ~/.kube/keos-jetson.yaml

export KUBECONFIG=~/.kube/keos-jetson.yaml
kubectl get nodes
# NAME            STATUS   ROLES                  AGE   VERSION
# keos-jetson-1   Ready    control-plane,master   3m    v1.30.4+k3s1
```

The default Kairos user is `kairos` (configurable in cloud-init). If
your image bundles k0s or RKE2 instead of k3s, the kubeconfig path
shifts — check `kairos-agent state` over SSH.

---

## 3. Install the Keos SRE Agent (one command, 12–25 min)

From your workstation (with `KUBECONFIG` exported):

```bash
curl -fsSL https://get.keos.pocteo.platform/install-edge.sh | bash
```

That's it. The script auto-detects:
- **Distro** (Talos vs Kairos vs other) — adjusts the Chaos Mesh
  socket path accordingly.
- **GPU** — enables it on Ollama if `nvidia.com/gpu` capacity
  shows up, otherwise CPU mode.
- **Target node** — single-node clusters resolve unambiguously;
  multi-node uses `--node <name>`.

Expected output:

```
[install-edge] Preflight…
[install-edge] ✓ kubectl + helm + openssl + python3 OK
[install-edge] Probing cluster…
[install-edge] ✓ distro: talos · k8s: v1.30.4 · nodes: 1
[install-edge] ✓ target node: keos-jetson-1 · GPU: yes (nvidia.com/gpu)
[install-edge] ✓ Talos detected — using containerd socket /run/containerd/containerd.sock
[install-edge] Generating bearer token + audit HMAC key…
[install-edge] ✓ tokens ready (operator copy cached at ~/.keos-token)
[install-edge] Adding the Keos Helm repo…
[install-edge] ✓ repo synced
[install-edge] Installing Keos to namespace 'keos'…
[install-edge] ✓ Helm release 'keos' deployed
[install-edge] Pulling LLM models (slow — ~5-10 min on first install)…
[install-edge] ✓ models cached on keos-jetson-1
[install-edge] Seeding the runbook corpus into Qdrant…
[install-edge] ✓ runbooks indexed

==================================================================
  Keos SRE Agent is up on talos.
==================================================================

  Open in your browser:
    http://192.168.1.42:30080

  Bearer token (paste it on the login screen):
    s6BlChxanIlS8HkgJ46kuTMGXcFnqAxG

  Cached at:  /home/you/.keos-token

  Helm release:  keos · namespace: keos
  Pinned to:     keos-jetson-1 (mode=hard, label=keos.pocteo/sre-home=true)
  Agent mode:    observe-only (flip to 'active' from Settings → Agent)
```

### What the script did

1. **Probed the cluster** — `kubectl version`, `kubectl get nodes -o json`.
2. **Auto-detected Talos** from node labels (`talos.dev/*`) and set
   the Chaos Mesh containerd socket to `/run/containerd/containerd.sock`
   (Talos default; differs from k3s).
3. **Picked the GPU-bearing node** and ran:
   ```
   helm upgrade --install keos pocteo/keos-sre-agent \
     --namespace keos --create-namespace \
     --set agent.attachment.mode=hard \
     --set agent.attachment.nodeName=keos-jetson-1 \
     --set agent.bootstrapNodeLabel.enabled=true \
     --set chaos-mesh.chaosDaemon.socketPath=/run/containerd/containerd.sock \
     --set ollama.gpu.enabled=true \
     --set ollama.gpu.runtimeClassName=nvidia
   ```
   The `bootstrapNodeLabel` hook labels `keos-jetson-1` with
   `keos.pocteo/sre-home=true` before the chart installs. The api +
   ollama Deployments select on that label — so if you ever rename
   the node or add more, the agent stays where you asked it to be.
3. **Waited up to 10 min** for everything Ready.
4. **Pulled the LLM models** via `kubectl exec` against the Ollama pod.
5. **Seeded the runbook corpus** via `kubectl exec` against the api pod.
6. **Resolved the access URL** — Ingress first, then NodePort, then a
   `kubectl port-forward` fallback. Talos and Kairos usually expose
   the agent via a NodePort on the Jetson's IP (the URL above).

The script is **idempotent** — re-running it does `helm upgrade --install`,
a no-op if nothing changed. Each step prints its command + exits
non-zero on failure; nothing's left half-broken.

---

## 4. First login (~3 min)

Open the URL the script printed in your browser. You land on the
token-paste screen:

```
┌─────────────────────────────────────────────────────────┐
│  Keos                                                    │
│  The Autonomous AI SRE Platform                          │
│                                                          │
│  Paste your bearer token:                                │
│  [_______________________________________________]       │
│                                                          │
│                                          [Sign in]       │
└─────────────────────────────────────────────────────────┘
```

Paste the token (also cached at `~/.keos-token` on your workstation).
You land on the dashboard.

The **Cluster context** card confirms what the agent discovered:

```
CLUSTER CONTEXT                                         [talos]
What the agent discovered about its host cluster.

  Kubernetes: v1.30.4              Runtime: containerd
  Nodes: 1/1 ready                 Capacity: 8 CPU · 8 GiB · 1 GPU

  NODES
  ● keos-jetson-1   ctrl/worker   [1 GPU]   [● agent here]   8 CPU · 8 GiB

  DETECTED OPERATORS
  [chaos_mesh ✓]   [argocd ✗]   [flux ✗]   [prometheus ✗]
  [cert_manager ✗]   [kube_state_metrics ✗]

  ⚠ No GitOps controller detected (Argo CD / Flux). Drift detection
    will fall back to filesystem-mounted manifests.
  ⚠ Prometheus not detected. Install kube-prometheus-stack to enable
    baselines + forecasts.
```

If the badge says `[talos]` (or `[keros]`) and `● agent here` appears
on the Jetson's row, **the install worked** — every component is
wired correctly, the agent is on the Jetson, the GPU is connected,
and the K8s API is observable.

---

## 5. Optional — install Prometheus + Argo CD

The agent works fine without them, but the smart layer (baselines,
forecasts, drift detection) is partly grounded by them.

### Prometheus (~2 min)

Enables ML baselines + predictive forecasts:

```bash
kubectl create namespace monitoring
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm install prom prometheus-community/kube-prometheus-stack \
  --namespace monitoring \
  --set alertmanager.enabled=false \
  --set grafana.enabled=false
```

The dashboard's Prometheus warning vanishes on the next 30 s probe.

### Argo CD (~3 min)

Enables GitOps drift detection:

```bash
kubectl create namespace argocd
kubectl apply -n argocd \
  -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
```

The "No GitOps controller detected" warning vanishes on the next
refresh.

> **Talos note.** Both of these install as Pods inside the cluster —
> they live in the same immutable-OS world. No node-side changes,
> no kernel modules, no sysfs writes.

---

## 6. Run your first incident (~90 s)

From the SPA's left sidebar: **Use Cases** → click **"Memory leak /
OOM trajectory"** → **Run**.

You'll see:

1. The Pipeline view auto-opens.
2. **Triage** → **Retrieve context** → **Cluster signals** →
   **Baselines** → **Diagnose** → **Plan action** → **Policy gate**
   light up green in sequence.
3. The LLM diagnoses the synthetic OOM trajectory (~20–30 s on a warm
   Jetson GPU, ~4 min on CPU).
4. Because the agent's default mode is `observe-only`, the action plan
   is logged but **not executed**.
5. The incident lands in **Incidents** with verdict `observe_only`.

Flip the agent to `active` (top-right of the dashboard → click the
`OBSERVE-ONLY` chip → confirm). Re-run the use case. This time the
agent actually executes the `rollout_undo` and **Verify** turns green
once the cluster recovers.

That's the full Observe → Predict → Heal → Learn loop, running
locally on your Jetson, ~15 min after first boot.

---

## 7. Alternative shapes (less common)

### Shape B — Jetson is a worker in an existing remote cluster

The control plane lives elsewhere; the Jetson is just one node in
that cluster. Install Keos from the workstation pointed at that
cluster's kubeconfig:

```bash
export KUBECONFIG=~/.kube/the-real-cluster.yaml
curl -fsSL https://get.keos.pocteo.platform/install-edge.sh \
  | bash -s -- --node keos-jetson-1
```

`--node keos-jetson-1` pins the agent to the Jetson — that's the box
with the GPU; inference runs there.

### Shape C — multiple Jetsons in HA

2–3 Jetsons form the control plane. The agent runs as 1 replica
pinned to one of them. Pass `--node <chosen-jetson>` and the
bootstrap hook labels that node.

The Helm chart's `replicaCount: 2` HA mode (planned in Phase 18) will
spread the agent across two Jetsons with NATS-backed leader election.
Until then, 1 replica + a stable node label is the right pattern.

### Shape D — Jetson is independent + you want it to manage another cluster

The agent **fundamentally runs inside the cluster it manages** —
that's how it reads the K8s API, watches kubelet events, and applies
remediations under scoped RBAC. There is no "point this Jetson at
that cluster" mode.

If you want the agent to manage a remote cluster, install it INTO
that cluster (Shape B). The Jetson can run a second cluster (its
own one-node Talos / Kairos) for an entirely separate agent if you
want a multi-tenant story — but that's two installs, two URLs, two
tokens.

---

## 8. Day-2 operations

Everything is in **Settings**:

- **Agent** — flip mode (observe-only → active → frozen); see
  pending approvals.
- **Calibration** — per-rule confidence thresholds (auto-learned;
  pin one to override).
- **Data lifecycle** — change retention windows live, no restart.
- **Drift** — recent GitOps drift events + the detector's config.
- **Forecasting / Baselines** — live tables of what the ML layer
  is tracking.
- **Notifications** — Slack + generic webhook for `page_human`.

Every operator action writes one HMAC-chained audit line. Verify
the chain any time via Settings → Audit → **Verify chain**.

---

## 9. Uninstall

From your workstation:

```bash
helm uninstall keos -n keos
kubectl delete namespace keos
```

Removes every resource — Pods, PVCs, Secrets, ConfigMaps, AgentMode
CR, the bootstrap node label. The Jetson is back to its post-flash
state. The OS itself is untouched.

If you're paranoid: clear `localStorage.shp_token` on the SPA's
origin in your browser and delete `~/.keos-token` on your
workstation.

---

## 10. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `kubectl get nodes` says "Unable to connect to the server" | Wrong kubeconfig OR Jetson unreachable on port 6443 | Re-run step 2; ping the Jetson; check that you're on the same network |
| `talosctl kubeconfig` errors with "certificate signed by unknown authority" | Wrong `--talosconfig` (operator PKI) | Use the talosconfig your OS-image builder shipped; do NOT hand-craft one |
| Install fails: "no matches for nvidia.com/gpu" | NVIDIA device plugin not running yet | `kubectl get ds -A` — if there's no nvidia-device-plugin DaemonSet, the OS image is broken; escalate to whoever flashed it |
| Ollama pod stuck Pending | Tried to schedule without the GPU runtime | `kubectl describe pod -n keos -l app=ollama` — look for "0/1 nodes are available". Re-run with `--no-gpu` first to confirm everything else works, then re-flash the OS image with a working device-plugin |
| Chaos Mesh's chaos-daemon CrashLoops | Wrong containerd socket path | The installer auto-sets the right path per distro (Talos: `/run/containerd/containerd.sock`; Kairos/k3s: `/run/k3s/containerd/containerd.sock`). If your OS image uses a non-default path, override: `helm upgrade keos -n keos --reuse-values --set chaos-mesh.chaosDaemon.socketPath=<your-path>` |
| The SPA loads but everything's "—" | Token wrong | Re-read the install script's final block; copy-paste fresh; OR `cat ~/.keos-token` |
| Dashboard never updates after action | api pod CrashLooping | `kubectl logs -n keos -l app.kubernetes.io/component=api --tail=50` |
| Pipeline runs forever | Ollama hasn't pulled models | `kubectl exec -n keos statefulset/ollama -- ollama list` — empty? re-run `install-edge.sh` (idempotent) |
| You can't open the SPA's URL | Jetson's NodePort blocked by firewall | `kubectl port-forward -n keos svc/caddy 8080:80` from your workstation and open `http://localhost:8080` |

For the full diagnostic, hit `/api/health/detailed`:

```bash
curl -H "Authorization: Bearer $(cat ~/.keos-token)" \
     http://<jetson-ip>:<port>/api/health/detailed | jq
```

Same checks the laptop demo's `make doctor` runs.

---

## Quick reference — distro-specific knobs

| Concern | Talos | Kairos (default k3s) | k3s (standalone, legacy) |
|---|---|---|---|
| Get kubeconfig | `talosctl --talosconfig … kubeconfig …` | `ssh kairos@<ip> 'sudo cat /etc/rancher/k3s/k3s.yaml'` + rewrite IP | `scp … /etc/rancher/k3s/k3s.yaml` + rewrite IP |
| Bundled K8s | k8s (kubelet, no shell) | **k3s** (Kairos default; k0s + RKE2 are options) | k3s |
| containerd socket (chaos-daemon) | `/run/containerd/containerd.sock` | `/run/k3s/containerd/containerd.sock` | `/run/k3s/containerd/containerd.sock` |
| RuntimeClass for GPU | `nvidia` (System Extension) | `nvidia` (Kairos NVIDIA bundle) | `nvidia` (device-plugin DaemonSet) |
| SSH to node | ✗ — `talosctl` only | ✓ — via cloud-init keys | ✓ — operator-provisioned |
| Install Keos from | workstation | workstation | workstation (or node) |

The installer applies the right delta automatically — this table is
just for diagnosing "why did the install pick X?" later.

Both immutable OSes are good fits. **Talos** wins on hardening
(no shell, no SSH, API-only management). **Kairos** wins on
flexibility (SSH for debugging, choice of k3s/k0s/RKE2, cloud-init
for first-boot config).
