# Keos SRE Agent v1.0.0

> **Status:** General Availability · **Date:** 2026-06 · **Prev:**
> [v1.0.0-rc.1](RELEASE_NOTES_1.0.0-rc.1.md)

The autonomous SRE agent that ships inside every edge Kubernetes
cluster — installed, running, and remediating real incidents.

This release closes the gap between "the chart renders" and "the
chart actually installs and runs on a real immutable-OS cluster".
Everything between rc.1 and 1.0 came from running the full stack
end-to-end against a live Talos cluster and fixing every gap the
production install path hit.

---

## What's new vs rc.1

### 🎯 Production edge target confirmed: **Kairos on Jetson Orin NX**

The recommended hardware + OS combination is now fully documented +
installable in one focused operator guide:

- **`docs/KAIROS_JETSON.md`** (new, canonical) — the day-1 install
  recipe. Hardware checklist, L4T SDK download, complete
  `defaults.yaml` cloud-config including:
  - Operator SSH key provisioning
  - EdgeVPN **peer-to-peer mesh** (`p2p.network_token`)
  - NVIDIA Container Toolkit installation via `stages.boot`
  - `nvidia` RuntimeClass + k8s device plugin
  - k3s args including `keos.pocteo/sre-home=true` node label
  - Auroraboot partition image build + the actual `flash.sh`
    invocation
- Single-node cluster + GPU inference verified end-to-end via
  `kubectl exec sts/ollama -- ollama ps` (PROCESSOR = 100% GPU).
- Multi-Jetson mesh: same cloud-config + flip `role: master` →
  `role: worker`. EdgeVPN bootstraps the inter-edge tunnel.

### 🛠 Six chart bugs fixed from the live Talos test

Every one of these was a real install-time failure on the test
cluster. None of them showed up in `helm template` or
`helm install --dry-run` — they only surface when the chart actually
runs on a real cluster. All six closed in this release:

| # | Symptom | Fix |
|---|---|---|
| **1** | `helm install` fails: `no matches for kind AgentMode in version security.poc/v1` | Moved the CRD from `templates/` to `crds/`; left only the default CR in `templates/agentmode-crd.yaml`. Helm always applies `crds/` first. |
| **2** | OPA pod crashes: `rego_parse_error: empty module` | Chart's OPA ConfigMap referenced `{{ .Files.Get "../../policies/agent.rego" }}` — Helm sandboxes `.Files.Get` to the chart dir. Copied rego into `chart/files/policies/` + fixed the path. |
| **3** | OPA still crashes after fix 2: `multiple default rules data.agent.authz.allow` | K8s ConfigMap mounts use a `..data/` symlink chain. OPA's recursive directory scan picked up agent.rego three times. Switched OPA's args to load a single file + use `subPath` mount. |
| **4** | `global.imageRegistry` re-prefixed stock images (ollama, caddy, nats, qdrant, opa) too | Helper template `pocteo.image` now takes an explicit `firstParty` bool. Stock images render verbatim; only `keos-api`, `keos-mcp-server`, `keos-frontend` get the global prefix. |
| **5** | Frontend pod CrashLoopBackOff: `nginx: bind() to 0.0.0.0:80 failed (13: Permission denied)` | Switched from `nginx:1.27-alpine` to `nginxinc/nginx-unprivileged:1.27-alpine` (listens on 8080 by default, runs as uid 101, has the right Linux file capabilities). |
| **6** | `bootstrapNodeLabel` hook silently never ran | Two bugs in one: filename had a leading underscore (`_node-label-hook.yaml`), which Helm treats as a partial template + skips. Plus `bitnami/kubectl:1.30` was removed from Docker Hub. Renamed file + switched to `alpine/k8s:1.30.0` (multi-arch, stable tag). |

The complete test methodology + the per-bug repro is in
[`docs/TALOS_LIVE_TEST.md`](TALOS_LIVE_TEST.md).

### 🩺 In-cluster runtime cleanups

- **`agent/serve.py`**: prefer `KEOS_API_PORT` env over `API_PORT`.
  K8s auto-injects `<SERVICE>_PORT=tcp://...` envs that broke
  `int(os.environ["API_PORT"])` and crashed api boot.
- **`agent/Dockerfile.api`**: stripped the baked
  `ENV KUBECONFIG=/root/.kube/config` (compose-mode leftover). In a
  pod kubectl now auto-uses the SA token at
  `/var/run/secrets/kubernetes.io/serviceaccount`.
- **`agent/api.py` `_kubectl_get`**: only passes `--kubeconfig` when
  the file actually exists. Same code path works on a laptop (file
  exists) and in a pod (file absent, in-cluster auth used).
- **`agent/api.py` `_check_cluster`**: tries `load_incluster_config()`
  first, falls back to `load_kube_config()` for the laptop dev path.
- **`deploy/helm/pocteo/templates/rbac.yaml`**: ClusterRole now
  grants the agent SA the verbs it actually needs:
  `customresourcedefinitions`, `namespaces`, `pods`, `events`,
  `deployments/statefulsets/daemonsets/replicasets` (cluster-scoped
  list), and `services/proxy` (for Prometheus apiserver-proxy reads).
  Without these the cluster-context probe returned `distro: unknown,
  nodes: 0, operators: []` on every real install.

### 📦 `deploy/helm/pocteo/values-talos-dev.yaml` (new, reference)

A reference values file with the Talos-specific overrides the
operator needs (chaos-mesh socket path). The file exists because
passing the path via `--set` through Git Bash on Windows mangles
the leading `/` into `C:/Program Files/Git/run/...`. Values files
are read by helm directly without shell expansion.

### 📝 Doc consolidation

- New: `docs/KAIROS_JETSON.md` (the canonical Jetson day-1)
- New: `docs/TALOS_LIVE_TEST.md` (test methodology + findings)
- Updated: `docs/INSTALL.md` §3 → Kairos is the recommended edge
  path
- Updated: `docs/JETSON.md` callout box at top points at Kairos
  doc
- Updated: `docs/JETSON_FIRST_BOOT.md` header redirects to Kairos
  doc
- Updated: `website/docs/platforms/kairos-jetson.md` mirror
- Updated: `README.md` doc index now leads with the Kairos doc

---

## Verified end-to-end on a real cluster

Live Talos v1.6.2 cluster (single-node, docker mode, 6 GB / 2 CPU):

| Component | Status |
|---|---|
| api / caddy / frontend / mcp-server / opa | ✅ 1/1 Ready |
| ollama (qwen2.5:3b + nomic-embed-text loaded) | ✅ Ready |
| qdrant (12 runbooks seeded) | ✅ Ready |
| nats (JetStream) | ✅ Ready |
| chaos-mesh (controller × 3 + chaos-daemon × 1 + chaos-dns) | ✅ All Running |
| Bootstrap node-label hook | ✅ Job runs + labels node + auto-cleans |

Health probe:
```
GET /api/health           → 200 ok / observe-only / 1.0.0
GET /api/mode             → 200 observe-only
GET /api/cluster/context  → distro: talos · nodes: 1/1 · operators: {chaos_mesh}
GET /api/health/detailed  → all 6 components OK
```

---

## Upgrade path from v1.0.0-rc.1

```bash
helm upgrade keos pocteo/keos-sre-agent \
    --kube-context <your-context> \
    --namespace keos \
    --reuse-values \
    --version 1.0.0
```

No values-schema changes. The CRD split (Finding 1) is handled by
Helm automatically — `crds/agentmode.yaml` is idempotent.

If you were depending on the **broken** `global.imageRegistry`
behavior (where it re-prefixed stock images too), set the per-image
`registry` overrides explicitly:

```yaml
ollama:
  image:
    repository: my-mirror.example.com/ollama/ollama
qdrant:
  image:
    repository: my-mirror.example.com/qdrant/qdrant
# ... etc
```

The chart used to do this silently for ALL images; now it only does
it for first-party images.

---

## Known limitations (will be addressed post-1.0)

| | Item | Phase |
|---|---|---|
| 🔄 | Continuous learning loop (micro/meso/macro) | Phase 16 |
| 🛑 | Pre-deploy auto-gating (admission webhook) | Phase 17 |
| 🌐 | Edge resilience: offline-first + HA + self-watchdog | Phase 18 |
| 🔐 | Deep security hardening: forward-secure HMAC, mTLS, signed runbooks | Phase 19 |
| 📊 | Confidence-tuning UI: A/B shadow runbooks, breakdown drill-down | Phase 20 |

See [`POSITIONING.md`](../POSITIONING.md) for the long-term
roadmap.

---

## Verification before tagging

```bash
# Chart lints cleanly
helm lint deploy/helm/pocteo

# Renders without `<no value>` substitutions
helm template keos deploy/helm/pocteo \
  --set-string auth.apiToken=test \
  --set-string auth.hmacKey=test \
  | grep -c '^kind:'                       # expect 66+

# install-edge.sh probe-only against Kairos/Talos-shaped JSON
bash scripts/install-edge.sh --probe-only  # exits 0, prints helm cmd

# Hermetic backend test suite
make test                                  # all green
```

---

## Highlights for the architect review

If a colleague is reading this to evaluate the project, the top
three things to look at:

1. **`docs/KAIROS_JETSON.md`** — proves there's a clear,
   end-to-end install path for the production hardware target,
   with no operator hand-holding required.
2. **`docs/TALOS_LIVE_TEST.md`** — proves the chart was actually
   tested against a real cluster (not just templated) + that every
   bug we found was fixed.
3. **`agent/graph.py`** — the LangGraph 10-node state machine. The
   heart of the autonomy story. Every node is well under 30 lines,
   every transition is gated through OPA, every action is
   HMAC-audited.
