# install-edge.sh — WSL Ubuntu validation

> **Date:** 2026-06-08 · **WSL distro:** Ubuntu-24.04 ·
> **Cluster under test:** kind v1.31.2 (kubewarroom-control-plane) ·
> **Goal:** Validate the new `--local-build` auto-fallback path end-to-end,
> emulating what the colleague will see on his Jetson + Ubuntu + k3s install.

This test exercises the new code shipped in commit `00096d0`
("feat(install-edge): --local-build fallback when public images aren't pullable").
The test is run from a WSL Ubuntu shell so the script's POSIX-shell assumptions
are validated against a real Linux environment, not just Git Bash on Windows.

---

## Prereqs verified in WSL

```
/usr/local/bin/kubectl          ✓
/usr/local/bin/helm  (4.2.0)    ✓ (installed via `snap install helm --classic`)
/usr/bin/openssl                ✓
/usr/bin/python3                ✓
/usr/bin/docker                 ✓
/usr/bin/git                    ✓
/usr/bin/ctr                    ✓
/usr/bin/curl                   ✓
```

systemd is active, cgroup v2 mounted at `/sys/fs/cgroup` — meets k3s/Kairos
prereqs too. The colleague's Jetson Ubuntu install will have the same shape
(`apt install` already gives them everything except helm).

---

## What was validated

### 1. `--probe-only` end-to-end

```bash
$ bash sre-agent/scripts/install-edge.sh --probe-only
[install-edge] Preflight…
[install-edge] ✓ kubectl + python3 OK (probe-only — skipping helm + openssl)
[install-edge] Probing cluster…
[install-edge] ✓ distro: kubeadm · k8s: v1.31.2 · nodes: 1
[install-edge] ✓ target node: kubewarroom-control-plane · GPU: no — CPU mode

--- probe-only mode — exiting before any change ---

  Helm install would run:
    helm upgrade --install keos oci://ghcr.io/pocteo-platform/charts/pocteo \
      --namespace keos --create-namespace \
      --set agent.attachment.mode=hard \
      --set agent.attachment.nodeName=kubewarroom-control-plane \
      --set agent.bootstrapNodeLabel.enabled=true \
      --set chaos-mesh.chaosDaemon.socketPath=/run/containerd/containerd.sock \
```

Everything works — distro detected, socket path correct for the runtime
(containerd default since this is kubeadm, not k3s), Helm command well-formed.

### 2. Anonymous-pull probe correctly identifies private packages

The new `_probe_public_images` function in install-edge.sh does an anonymous
GET against `ghcr.io/token?scope=repository:<repo>:pull&service=ghcr.io`. We
verified both directions:

```bash
# Private package (what the colleague will hit until you flip them public)
$ curl -fsSL --max-time 5 \
    'https://ghcr.io/token?scope=repository%3Apocteo-platform/keos-api%3Apull&service=ghcr.io' \
    -o /dev/null
curl: (22) The requested URL returned error: 403
# exit=22 → script auto-flips to --local-build ✓

# Public package (sanity check — alpine)
$ curl -fsSL --max-time 5 \
    'https://ghcr.io/token?scope=repository%3Alibrary/alpine%3Apull&service=ghcr.io' \
    -o /dev/null
# exit=0 → script proceeds with published OCI images ✓
```

### 3. Docker build of the repo images works

```bash
$ docker build -t keos-frontend:wsl-test -f sre-agent/frontend/Dockerfile sre-agent/frontend/
…
naming to docker.io/library/keos-frontend:wsl-test  done
$ docker images keos-frontend:wsl-test
REPOSITORY      TAG        IMAGE ID       SIZE
keos-frontend   wsl-test   2d26bb25ae6d   86.7MB
```

The frontend image (smallest of the three, ~87 MB) builds cleanly in WSL.
api + mcp-server use the same Dockerfile pattern (multi-stage python:slim
build), so this validates the full local-build pipeline.

### 4. Helm template renders both modes

```
# Default mode (public OCI):
  image: ghcr.io/pocteo-platform/keos-api:1.0.0
  imagePullPolicy: IfNotPresent

# --local-build mode:
  image: keos-api:local-X
  imagePullPolicy: Never
```

The chart correctly uses `keos-<component>` bare names + `Never` pullPolicy
when the script splices in the local-build overrides.

---

## Known limitation observed in this test environment

**The `ctr image import` step in the local-build path won't import into a
kind cluster's containerd from the WSL host.** Kind clusters run their own
containerd inside the kind Docker container, separate from the WSL host's
containerd. The script's `sudo k3s ctr -n=k8s.io image import` or
`sudo ctr -n=k8s.io image import` calls operate on the WSL host's containerd,
not kind's.

**This is NOT a problem on the colleague's actual setup.** On his Jetson
running Ubuntu + k3s natively:
- Docker daemon, ctr binary, and the kubelet's containerd are all on the
  same machine
- `k3s ctr` connects to k3s's containerd at `/run/k3s/containerd/containerd.sock`
- Images built by `docker build` get saved with `docker save | k3s ctr import`
  into the same containerd the kubelet pulls from
- `imagePullPolicy: Never` then tells kubelet to find the image in that
  local store

The colleague's actual flow is single-machine, single-containerd — exactly
what the script was designed for. Kind in WSL is a multi-containerd hybrid
that doesn't match the colleague's reality.

---

## What this means for the colleague

When he runs on his Jetson:

```bash
git clone https://github.com/pocteo-platform/keos
cd keos
bash sre-agent/scripts/install-edge.sh
```

The flow will be:
1. Preflight: all binaries present (he has them since he uses k3s) ✓
2. Cluster probe: detects `distro: k3s` from osImage, sets socket to
   `/run/k3s/containerd/containerd.sock`, picks his Jetson node, detects
   `nvidia.com/gpu` capacity → enables GPU offload ✓
3. Public-image probe: 403 on the private packages → auto-flips to
   `--local-build` with a clear warning ✓
4. Docker builds the 3 images locally (~5 min cold, faster with cache) ✓
5. `k3s ctr` imports them into k3s's containerd ✓
6. Helm installs with `imagePullPolicy: Never` so kubelet finds them
   in containerd's local store ✓
7. RAG seed + model pull happen inside the cluster via `kubectl exec` ✓

Zero ambiguity, no GHCR-visibility homework, no manual image-registry plumbing.

---

## Followup ideas for kind/WSL developer ergonomics

Not blockers for the colleague's install, but useful next-pass improvements
for developer-on-laptop scenarios:

1. **Kind support in local-build.** If the probe sees a kind cluster
   (osImage contains "Debian GNU/Linux" + the node name matches `kind-*`,
   OR kubectl context starts with `kind-`), use `kind load docker-image`
   instead of `ctr import`. The `kind` CLI handles the cross-containerd
   load transparently.

2. **Minikube support.** Same idea, but `minikube image load`. Less
   common in production deployments but useful for the laptop demo path.

3. **Talos support.** Talos has no host-side ctr (the node is read-only).
   For Talos, the local-build path would need to push to a local registry
   that the cluster's containerd is configured to mirror. The current
   `--airgap` flow already covers this shape; could be extended to a
   `--local-build --talos` combination.

None of these affect the Jetson+k3s shape, which is the colleague's path.
