# Fleet registry — the standard production install flow

Production devices install Keos entirely from a **self-hosted registry**: the Helm
chart, the three first-party images, the Jetson ollama variant, and every pinned
third-party image. No device ever needs docker, a repo clone, or ghcr.io access.

```
workstation                                 device (k3s, Ubuntu/Debian)
───────────                                 ───────────────────────────
scripts/release.sh all  ──► registry ◄──   bash install-edge.sh \
  (build + mirror + chart + verify)            --image-registry <host:port> [--gpu]
```

## 1 · Stand up a registry

Any OCI registry works (Harbor, Nexus, JFrog…). The minimal option:

```bash
# TLS (recommended) — bring a cert for the registry hostname:
docker run -d --restart=always --name registry -p 5000:5000 \
  -v /opt/registry/data:/var/lib/registry \
  -v /opt/registry/certs:/certs \
  -e REGISTRY_HTTP_TLS_CERTIFICATE=/certs/registry.crt \
  -e REGISTRY_HTTP_TLS_KEY=/certs/registry.key \
  registry:2

# Plain HTTP (lab only) — drop the TLS env/volumes; then use
# `release.sh --insecure` and `install-edge.sh --plain-http`.
```

## 2 · Populate it (workstation)

```bash
# one-time: arm64 emulation for multi-arch builds
docker run --privileged --rm tonistiigi/binfmt --install arm64

REGISTRY=registry.fleet.local:5000 scripts/release.sh all          # TLS
REGISTRY=registry.fleet.local:5000 scripts/release.sh all --insecure  # plain HTTP
```

What lands in the registry:

| Artifact | Path |
|---|---|
| chart | `oci://<reg>/charts/pocteo:<version>` |
| first-party | `<reg>/pocteo-platform/keos-{api,mcp-server,frontend}:<version>` |
| Jetson ollama | `<reg>/pocteo-platform/keos-ollama-jetson:<ollama-ver>-jp6` |
| third-party | `<reg>/<original path>` (e.g. `library/caddy`, `falcosecurity/falco`, `cilium/tetragon`, `chaos-mesh/...`) — multi-arch manifests preserved via skopeo |

`release.sh verify` (also part of `all`) runs the multi-arch audit
(`scripts/audit-images.sh`) so an amd64-only image can never ship silently.

## 3 · Trust the registry on each device (k3s)

k3s containerd does not read `/etc/docker/`; it has its own config:

```yaml
# /etc/rancher/k3s/registries.yaml
mirrors:
  registry.fleet.local:5000:
    endpoint: ["https://registry.fleet.local:5000"]
configs:
  registry.fleet.local:5000:
    tls:
      ca_file: /etc/keos/registry-ca.crt
    # Plain-HTTP lab registry instead:
    #   endpoint: ["http://registry.fleet.local:5000"] (above) and
    #   tls: {insecure_skip_verify: true}
```

```bash
sudo systemctl restart k3s
```

The workstation-side tools need the same trust: import the CA into the system
store, or use `helm --plain-http` / `skopeo --dest-tls-verify=false` (wrapped by
the `--insecure` / `--plain-http` flags).

## 4 · Install on the device

```bash
bash install-edge.sh --image-registry registry.fleet.local:5000 [--gpu] [--plain-http]
```

The installer probes `oci://<registry>/charts/pocteo` first; when found, it skips
every public probe and redirects **all** images (first-party via
`global.imageRegistry`, stock + falco/tetragon/chaos subcharts via per-image
overrides — see `apply_registry_overrides()` in `scripts/install-edge.sh`).

## Upgrades

Bump `deploy/helm/pocteo/Chart.yaml` version → `release.sh all` → on devices:
`keosctl upgrade --chart-version <new>` (or re-run install-edge.sh).

## Notes

- **Kernel-bound falco probes** (BTF-less kernels like Jetson L4T) are built and
  pushed separately by `scripts/build-falco-probe.sh` — see `docs/SECURITY_SENSORS.md`.
- `build-airgap-bundle.sh` produces a tarball for fully offline sites; its image
  list comes from `release.sh images-list` (same single source of truth).
