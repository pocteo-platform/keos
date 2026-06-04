# Talos live-deploy test — findings + fixes

> **Date:** 2026-06-04 · **OS:** Talos v1.6.2 (single-node, Docker
> mode via `talosctl cluster create`) · **K8s:** v1.29.0 · **Goal:**
> end-to-end verify that the Helm chart actually installs + the agent
> survives a real Talos install — not just `helm template`. Mirrors
> the Jetson+Talos scenario the operator will hit when the hardware
> arrives.

This test surfaced **six concrete chart gaps** that would have bitten
the first real Jetson install. Two were fixed in this session;
the other four are tracked as follow-up issues.

---

## What was tested

```bash
# 1. Tear down compose stack (preserve ollama-models named volume)
docker compose -f sre-agent/docker-compose.yml down

# 2. Spin up local Docker registry (host port 5000)
docker run -d --restart=always --name keos-registry -p 5000:5000 \
  -v keos-registry-data:/var/lib/registry registry:2

# 3. Spin up single-node Talos in Docker
talosctl cluster create --name keos-talos --workers 0 --masters 1 \
  --memory 4096 --cpus 2

# 4. Build + push the 3 Pocteo images to local registry
docker build -t localhost:5000/pocteo-api:0.1.0          -f sre-agent/agent/Dockerfile.api  sre-agent
docker build -t localhost:5000/pocteo-mcp-server:0.1.0   -f sre-agent/mcp_server/Dockerfile sre-agent
docker build -t localhost:5000/pocteo-frontend:0.1.0     -f sre-agent/frontend/Dockerfile   sre-agent/frontend
docker push localhost:5000/pocteo-api:0.1.0
docker push localhost:5000/pocteo-mcp-server:0.1.0
docker push localhost:5000/pocteo-frontend:0.1.0
# Also mirror stock images (caddy / ollama / qdrant / nats / opa)
# because the chart's global.imageRegistry override applies to ALL
# images, not just Pocteo's — see Finding 4.

# 5. Patch Talos to (a) trust the insecure local registry and
#    (b) allow scheduling on the single control-plane node
talosctl patch machineconfig -p '@talos-patch.yaml'

# 6. Helm install
helm install keos sre-agent/deploy/helm/pocteo \
  --namespace keos --create-namespace \
  --set-string auth.apiToken="$(openssl rand -base64 32)" \
  --set-string auth.hmacKey="$(openssl rand -base64 32)" \
  --set agent.attachment.mode=hard \
  --set-string agent.attachment.nodeName=keos-talos-controlplane-1 \
  --set agent.bootstrapNodeLabel.enabled=true \
  --set-string global.imageRegistry=10.5.0.1:5000 \
  --set chaos-mesh.chaosDaemon.socketPath=/run/containerd/containerd.sock

# 7. Install Rancher local-path-provisioner (default StorageClass)
kubectl apply -f https://raw.githubusercontent.com/rancher/local-path-provisioner/master/deploy/local-path-storage.yaml
kubectl patch storageclass local-path -p '{"metadata":{"annotations":{"storageclass.kubernetes.io/is-default-class":"true"}}}'
```

## Outcome

After the two fixes shipped in this session:

| Component | Status |
|---|---|
| **Talos cluster** | ✅ Bootstraps cleanly in ~90s, exposes K8s API on 127.0.0.1:6443 |
| **install-edge.sh probe** | ✅ Correctly detects `distro: talos` via `osImage` fingerprint |
| **install-edge.sh helm install line** | ✅ Sets `chaos-mesh.chaosDaemon.socketPath=/run/containerd/containerd.sock` (the Talos path) |
| **Helm chart install** | ✅ All 66 K8s objects created on Talos's API server |
| **Caddy ingress proxy** | ✅ Running |
| **MCP server** | ✅ Running |
| **OPA policy engine** | ✅ Running (after Findings 1+2 fix) |
| **Chaos Mesh** | ✅ All 4 pods Running on Talos (controller-manager × 3 + chaos-daemon × 1) |
| **API + Frontend + Ollama + Qdrant + NATS** | ❌ Blocked by Findings 3–6 (separate gaps, not Talos-specific) |

The deploy framework is sound. The remaining failures are real chart
bugs, not Talos compatibility problems.

---

## Findings

### Finding 1 — CRD + CR in the same template breaks `--dry-run=server` + race-condition on real install · **FIXED**

**Symptom.** Helm install fails with:
```
unable to build kubernetes objects from release manifest:
resource mapping not found for name: "default" namespace: ""
from "": no matches for kind "AgentMode" in version "security.poc/v1"
ensure CRDs are installed first
```

**Cause.** `deploy/helm/pocteo/templates/agentmode-crd.yaml` contained
BOTH the CRD definition AND the default `AgentMode` CR. Helm sorts by
kind on install, but server-side validation runs against the live
API server — which doesn't yet know about the `security.poc/v1`
group when the CR validation runs.

**Fix.** Split into two:
- `deploy/helm/pocteo/crds/agentmode.yaml` — plain CRD YAML, no Helm
  templating. Helm installs `crds/` BEFORE any template.
- `deploy/helm/pocteo/templates/agentmode-crd.yaml` — just the CR,
  with `{{- include "pocteo.labels" ... }}` templating preserved.

### Finding 2 — OPA ConfigMap is empty because `.Files.Get` can't escape the chart directory · **FIXED**

**Symptom.** OPA pod crashes on every start with:
```
load error: 3 errors occurred during loading:
/policies/..2026_06_04_18_01_46.3403970771/agent.rego:0: rego_parse_error: empty module
/policies/..data/agent.rego:0: rego_parse_error: empty module
/policies/agent.rego:0: rego_parse_error: empty module
```

**Cause.** `templates/opa-cm.yaml` referenced
`{{ .Files.Get "../../policies/agent.rego" }}`. Helm sandboxes
`.Files.Get` to files INSIDE the chart directory; anything outside
silently returns the empty string, so the ConfigMap shipped a 0-byte
`agent.rego`. OPA then crash-looped with `rego_parse_error: empty
module` × 3 (one for each symlinked copy of the file from the
Kubernetes mount path).

**Fix.** Two changes:
1. Copy `policies/agent.rego` into the chart at
   `deploy/helm/pocteo/files/policies/agent.rego`.
2. Update `templates/opa-cm.yaml` to
   `{{ .Files.Get "files/policies/agent.rego" }}`.

### Finding 3 — Kubernetes ConfigMap `..data` symlinks trick OPA's directory scan · **FIXED**

**Symptom.** Even after Finding 2 is fixed, OPA still crashes:
```
rego_type_error: multiple default rules data.agent.authz.allow found
```

**Cause.** When K8s mounts a ConfigMap as a directory, it builds a
symlink chain: `/policies/agent.rego → ../..data/agent.rego →
../..2026_06_04_..../agent.rego`. OPA's `opa run /policies` recursive
directory scan picks up the file three times (one per symlink) and
declares duplicate `default allow` rules.

**Fix.** Switch from a directory scan to a single-file load:
- `args` changed from `["run", "--server", ..., "/policies"]` to
  `["run", "--server", ..., "/policies/agent.rego"]`
- Volume mount switched from `mountPath: /policies` (whole dir, with
  symlinks) to `mountPath: /policies/agent.rego` with `subPath: agent.rego`
  (just the file, no symlinks involved).

### Finding 4 — `global.imageRegistry` is applied to stock images too · **TODO**

**Symptom.** `helm install ... --set global.imageRegistry=10.5.0.1:5000`
rewrites our 3 Pocteo images correctly (`pocteo-api`, `pocteo-mcp-server`,
`pocteo-frontend`) — but ALSO rewrites the stock images:
- `caddy:2.8-alpine` → `10.5.0.1:5000/caddy:2.8-alpine`
- `ollama/ollama:0.4.7` → `10.5.0.1:5000/ollama:0.4.7`
- `qdrant/qdrant:v1.12.4` → `10.5.0.1:5000/qdrant:v1.12.4`
- `nats:2.10.22-alpine` → `10.5.0.1:5000/nats:2.10.22-alpine`
- `openpolicyagent/opa:0.69.0` → `10.5.0.1:5000/opa:0.69.0`

That's wrong: stock images come from their upstream registries (docker.io
mostly). A registry mirror should only re-host them; the chart shouldn't
unconditionally re-prefix.

**Workaround used in this test.** `docker pull` + `docker tag` + `docker push`
each stock image into the local registry under the bare names the chart
expects after re-prefixing (`caddy:2.8-alpine`, `ollama:0.4.7`, etc.).

**Recommended fix.** Helper template `pocteo.image` should distinguish
"first-party images we publish" (apply global.imageRegistry override)
from "upstream stock images" (don't override; only honor a per-image
`registry:` override).

### Finding 5 — Frontend nginx can't bind to port 80 under the restricted SecurityContext · **TODO**

**Symptom.** Frontend pod CrashLoopBackOff with:
```
nginx: [emerg] bind() to 0.0.0.0:80 failed (13: Permission denied)
```

**Cause.** Chart's `frontend.yaml` Deployment sets
`securityContext.runAsNonRoot: true` + `runAsUser: 1000`. Non-root
users can't bind to privileged ports (<1024). The frontend image
ships an nginx config that listens on 80.

**Recommended fix.** Either:
- Change the frontend image's nginx config to listen on `8080` and
  update the chart's Service to `targetPort: 8080`.
- OR add `securityContext.capabilities.add: ["NET_BIND_SERVICE"]` so
  nginx (running as uid 1000) can bind to <1024.

The 8080 path is cleaner and matches the conventional non-root nginx
pattern.

### Finding 6 — `bootstrapNodeLabel` hook silently doesn't run · **TODO**

**Symptom.** With `agent.bootstrapNodeLabel.enabled=true` set, no Job
ever runs in the `keos` namespace + the target node never gets the
`keos.pocteo/sre-home=true` label. The api / ollama / qdrant /
nats Deployments + StatefulSets then can't schedule because their
`nodeSelector: keos.pocteo/sre-home: "true"` matches nothing.

**Cause.** Not fully diagnosed. The `templates/_node-label-hook.yaml`
file is referenced in the chart, but the Helm hook never instantiated
a Job in the test. May be a templating gate or a hook-weight ordering
issue.

**Workaround used in this test.** `kubectl label node
keos-talos-controlplane-1 keos.pocteo/sre-home=true --overwrite`
manually after install.

**Recommended fix.** Audit `templates/_node-label-hook.yaml` —
verify the Job has correct `helm.sh/hook` annotations + that the
ServiceAccount has the needed RBAC.

---

## What still needs the operator (in this Talos test scenario)

Two Talos-specific environment knobs the operator applied manually:

```yaml
# Talos machineconfig patch
cluster:
  allowSchedulingOnControlPlanes: true   # single-node Talos clusters
machine:
  registries:
    mirrors:
      10.5.0.1:5000:                     # the host's local registry
        endpoints:
          - http://10.5.0.1:5000         # HTTP, not HTTPS
```

Plus a default StorageClass (the chart's StatefulSets need one):

```bash
kubectl apply -f https://raw.githubusercontent.com/rancher/local-path-provisioner/master/deploy/local-path-storage.yaml
kubectl patch storageclass local-path \
  -p '{"metadata":{"annotations":{"storageclass.kubernetes.io/is-default-class":"true"}}}'
```

For a real Jetson+Talos production install, the OS-image team handles
all three at build time — they're not operator-time concerns. This
test just exposed the dependency chain.

---

## Teardown

```bash
helm uninstall keos -n keos
kubectl delete namespace keos
talosctl cluster destroy --name keos-talos
docker rm -f keos-registry
docker volume rm keos-registry-data
```
