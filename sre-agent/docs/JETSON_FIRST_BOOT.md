# Jetson Orin Nano — first-boot install walkthrough

> **Scenario.** A Jetson Orin Nano arrives preconfigured with the Keos
> edge OS. You plug it into ethernet + a screen. This doc tells you
> exactly what you'll see, type, and click — in order — to get the
> Keos SRE Agent running on that box and accept its first cluster
> incident.
>
> **Total time:** ~12 minutes on a stock Orin Nano with the model
> images already in the OS layer, ~25 minutes on the first install
> if Ollama has to pull `qwen2.5:3b-instruct-q4_K_M` (~1.9 GB) over
> the wire.

---

## 0. What the OS gives you before you start

The Keos edge OS image ships with:

- A single-node **k3s** cluster, pre-installed, started at boot. The
  Jetson is its own control plane + only worker.
- The **NVIDIA Container Toolkit** wired into containerd, so any pod
  that requests `nvidia.com/gpu` lands on the Jetson's GPU.
- A **`nvidia` RuntimeClass** + the **NVIDIA device plugin DaemonSet**
  so kubelet advertises `nvidia.com/gpu: 1` as a schedulable resource.
- The standard k3s tools on `PATH`: `kubectl`, `k3s`, `crictl`.
- `helm` 3.16+ + `git`.
- An `operator` user with passwordless sudo.

What it does NOT have:

- The Keos SRE Agent (you install it).
- Argo CD / Flux / Prometheus (optional; the agent detects them if
  you do install them later).

> **First-boot quick check.** If anything below isn't true, escalate
> to whoever flashed the OS — you shouldn't have to fix it yourself:
> ```bash
> kubectl get nodes
> # NAME            STATUS   ROLES                  AGE     VERSION
> # keos-jetson-1   Ready    control-plane,master   2m      v1.30.4+k3s1
>
> kubectl get nodes -o jsonpath='{.items[0].status.capacity.nvidia\.com/gpu}'
> # 1
>
> kubectl get runtimeclass
> # NAME     HANDLER   AGE
> # nvidia   nvidia    2m
> ```

---

## 1. Connect to the Jetson (60 seconds)

You have two equally good options:

### Option A — directly on the attached screen
The OS boots into a TTY (no GUI). Login as `operator`:
```
Keos edge node · keos-jetson-1
operator login: operator
Password: ********
```

### Option B — SSH from your laptop
Find the Jetson's IP from your router's DHCP page (or via mDNS):
```bash
# from your laptop
ping keos-jetson-1.local
# 64 bytes from 192.168.1.42 ...

ssh operator@192.168.1.42
```

SSH is nicer because you can copy-paste — recommended for the install.

---

## 2. Install the Keos SRE Agent (one command)

```bash
curl -fsSL https://get.keos.pocteo.platform/install-edge.sh | bash
```

That's it. The script does five things, prints what it's doing as it
goes, and stops at any error:

```
[install-edge] Detecting Kubernetes context...
[install-edge]   distro: k3s · k8s: v1.30.4 · nodes: 1 (keos-jetson-1)
[install-edge]   GPU: nvidia.com/gpu = 1 ✓
[install-edge] Generating bearer token + audit HMAC key...
[install-edge] Adding the Keos Helm repo...
[install-edge] Installing Keos to namespace 'keos'...
[install-edge]   helm install keos pocteo/keos-sre-agent \
                  --set auth.apiToken=*** \
                  --set auth.hmacKey=*** \
                  --set agent.attachment.mode=hard \
                  --set agent.attachment.nodeName=keos-jetson-1 \
                  --set ollama.gpu.enabled=true \
                  --set ollama.gpu.runtimeClassName=nvidia
[install-edge] Waiting for pods to become ready (~3 min cold-start)...
[install-edge]   keos-api          1/1 Ready
[install-edge]   keos-mcp-server   1/1 Ready
[install-edge]   keos-ollama       1/1 Ready
[install-edge]   keos-qdrant       1/1 Ready
[install-edge]   keos-nats         1/1 Ready
[install-edge]   keos-opa          1/1 Ready
[install-edge] Pulling models (this is the slow step — ~5-10 min)...
[install-edge]   ollama pull qwen2.5:3b-instruct-q4_K_M ✓
[install-edge]   ollama pull nomic-embed-text ✓
[install-edge] Seeding the runbook corpus into Qdrant... ✓
[install-edge] Bootstrap complete!

==================================================================
  Open Keos in your browser:
    http://192.168.1.42

  Your bearer token (paste it on the login screen):
    s6BlChxanIlS8HkgJ46kuTMGXcFnqAxG

  This token is also saved to /home/operator/.keos-token
==================================================================
```

### What the script just did under the hood

1. **`kubectl version`** to confirm the cluster is reachable.
2. **`kubectl get nodes -o yaml`** to find the Jetson's name + GPU capacity.
3. Generated a random `apiToken` + `hmacKey` (`openssl rand -base64 32`).
4. Ran the Helm install with:
   - `agent.attachment.mode=hard` → the api pod **only** schedules on
     this node. (One-Jetson clusters have one node, but pinning here
     is good hygiene for when you add a second.)
   - `ollama.gpu.enabled=true` + `runtimeClassName=nvidia` → Ollama
     gets the GPU.
5. Waited for all 6 Deployments to report Ready.
6. Pulled the LLM models via the Ollama pod's exec.
7. Seeded the 12 SRE runbooks into Qdrant via the api pod's exec.

The whole thing is **idempotent** — re-run it and it's a no-op
(`helm upgrade --install`). If a step fails, the script prints the
failing command + exit code; nothing's left half-broken.

---

## 3. First login — what you'll see (~3 min)

Open `http://<jetson-ip>` on your laptop. The first screen is the
token-paste page:

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

Paste the token from the install script. You land on the dashboard.

The **Cluster context** card (bottom of the dashboard) confirms the
agent saw everything:

```
CLUSTER CONTEXT                                           [k3s]
What the agent discovered about its host cluster.

  Kubernetes: v1.30.4+k3s1     Runtime: containerd
  Nodes: 1/1 ready             Capacity: 8 CPU · 8 GiB · 1 GPU

  NODES
  ● keos-jetson-1   ctrl   [1 GPU]   [● agent here]   8 CPU · 8 GiB

  DETECTED OPERATORS
  [chaos_mesh ✓]   [argocd ✗]   [flux ✗]   [prometheus ✗]
  [cert_manager ✗]   [kube_state_metrics ✗]

  ⚠ No GitOps controller detected (Argo CD / Flux). Drift detection
    will fall back to filesystem-mounted manifests.
  ⚠ Prometheus not detected. Install kube-prometheus-stack to enable
    baselines + forecasts.
```

If you see this, **the install worked** — every component is wired
correctly, the agent knows it's on the Jetson, GPU acceleration is
on, and the agent is observing the cluster.

---

## 4. Optional — install Prometheus + Argo CD (so warnings disappear)

The Keos agent runs fine without these, but it's smarter with them:

### Prometheus (~2 min)
Enables Phase 13 baselines + Phase 14 forecasts:

```bash
kubectl create namespace monitoring
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm install prom prometheus-community/prometheus -n monitoring \
  --set alertmanager.enabled=false \
  --set pushgateway.enabled=false
```

Wait for the prometheus + kube-state-metrics pods to be Ready, then
**refresh the dashboard**. The two warnings disappear automatically —
the agent re-probes every 30 s.

### Argo CD (~3 min)
Enables Phase 15 GitOps drift detection:

```bash
kubectl create namespace argocd
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
```

The "No GitOps controller detected" warning vanishes on the next
dashboard refresh. Configure your Argo CD applications normally;
the agent reads from them.

---

## 5. Run your first incident (90 s)

From the SPA's left sidebar: **Use Cases** → click **"Memory leak /
OOM trajectory"** → **Run**.

You'll see:

1. The Pipeline view auto-opens.
2. **Triage** → **Retrieve context** → **Cluster signals** →
   **Baselines** → **Diagnose** → **Plan action** → **Policy gate**
   light up green in sequence.
3. The agent diagnoses the synthetic OOM trajectory (the LLM call
   takes ~20–30 s on a warm Jetson GPU; ~4 min on CPU).
4. Because the agent's default mode is `observe-only`, the action
   plan is logged but not executed.
5. The incident lands in **Incidents** with verdict `observe_only`.

Flip the agent to `active` (top-right of the dashboard → click the
`OBSERVE-ONLY` chip → confirm). Re-run the use case. This time the
agent actually executes the `rollout_undo` and **Verify** turns green
once the cluster recovers.

That's the full Observe → Predict → Heal → Learn loop, running
locally on your Jetson, ~15 minutes after you plugged in the
ethernet cable.

---

## 6. What if the Jetson is NOT the cluster

The walkthrough above assumes the Jetson runs its own single-node
k3s. Three other shapes are possible:

### Shape B — Jetson is a worker in an existing remote cluster
Your cluster's control plane lives elsewhere (datacenter, AWS, etc.)
and the Jetson is just one node in it. **Don't** run `install-edge.sh`
on the Jetson — the agent should run as a Pod inside the cluster,
which means you install it from your laptop pointed at the right
kubeconfig:

```bash
# from your laptop, with $KUBECONFIG pointing at the real cluster
helm install keos pocteo/keos-sre-agent \
  --set auth.apiToken=$(openssl rand -base64 32) \
  --set auth.hmacKey=$(openssl rand -base64 32) \
  --set agent.attachment.mode=hard \
  --set agent.attachment.nodeName=keos-jetson-1 \
  --set ollama.gpu.enabled=true \
  --set ollama.gpu.runtimeClassName=nvidia
```

The `agent.attachment.mode=hard` + `nodeName=keos-jetson-1` pins the
agent to the Jetson — that's the box with the GPU, so inference runs
there. Other workloads stay where they were.

### Shape C — multiple Jetsons forming a small k3s HA cluster
Same as Shape B, but the control plane is the Jetsons themselves
(2–3 of them in HA mode). Run `install-edge.sh` from the operator's
choice of Jetson; the script auto-picks the local node, but you
should override:

```bash
curl -fsSL https://get.keos.pocteo.platform/install-edge.sh \
  | bash -s -- --node keos-jetson-2
```

The agent ends up on `keos-jetson-2` regardless of which Jetson you
ran the script from.

### Shape D — Jetson is independent + you want to point it at another cluster
The SRE agent fundamentally runs *inside* a cluster, not adjacent to
one — that's how it reads the K8s API, watches kubelet events, and
applies remediations under scoped RBAC. There is no "point this
Jetson at that cluster" mode; if you want the agent to manage a
remote cluster, install it INTO that cluster. The Jetson can run a
SECOND cluster (its own one-node k3s) for an entirely separate
agent if you want a multi-tenant story — but that's two installs,
two URLs, two tokens.

---

## 7. Day 2 — what an operator does after install

Everything's in **Settings**:

- **Agent** — flip mode (observe-only → active → frozen), see
  pending approvals.
- **Calibration** — per-rule confidence thresholds. Pin one when
  you want to override the auto-learner.
- **Data lifecycle** — change retention windows (audit / incidents /
  traces). Live, no restart.
- **Drift** — recent GitOps drift events + the detector's config.
- **Forecasting / Baselines** — live tables of what the ML layer is
  tracking. Enable / disable via env vars (Helm `values.yaml`).
- **Notifications** — Slack + generic webhook for `page_human` events.

Every action you take here writes one line to the HMAC-chained audit
log. You can verify the chain at any time via Settings → Audit →
**Verify chain**.

---

## 8. Uninstall — the operator's escape hatch

```bash
helm uninstall keos -n keos
kubectl delete namespace keos
```

This removes every resource — Pods, PVCs, Secrets, ConfigMaps, the
AgentMode CR, the bootstrap node label. The Jetson is back to its
post-flash state.

If you're paranoid: also remove the bearer token cached in your
laptop's browser (clear `localStorage.shp_token` on the SPA's origin)
and the token file on disk (`/home/operator/.keos-token`).

---

## 9. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `kubectl get nodes` says "connection refused" | k3s not started | `sudo systemctl start k3s` then re-run the script |
| Helm install fails: "no matches for nvidia.com/gpu" | NVIDIA device plugin missing | `kubectl get ds -n kube-system` — if there's no `nvidia-device-plugin-daemonset`, the OS image is broken; escalate |
| Ollama pod stuck Pending | Tried to schedule without GPU runtime | `kubectl describe pod -n keos -l app=ollama` — look for "0/1 nodes are available". Re-install with `ollama.gpu.runtimeClassName=nvidia` |
| The SPA loads but everything's "—" | Token wrong | Open the install script's output again; copy-paste fresh into the login page |
| Dashboard never updates | API container CrashLooping | `kubectl logs -n keos -l app=api --tail=50` — usually a missing env var |
| Pipeline pings then nothing happens | LLM hasn't pulled yet | `kubectl exec -n keos -it keos-ollama-0 -- ollama list` — if empty, re-run the model-pull step from the script |

If `make doctor`-equivalent diagnostics matter to you, the same flow
is on the SPA's `/api/health/detailed` endpoint:

```bash
curl -H "Authorization: Bearer $(cat ~/.keos-token)" \
  http://<jetson-ip>/api/health/detailed | jq
```

That's the canonical "all systems go" report — same checks the
laptop demo's `make doctor` runs.
