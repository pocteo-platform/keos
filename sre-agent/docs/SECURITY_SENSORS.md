# Security sensors — capability ladder & prebuilt probes

Keos's live runtime security (reverse shells, miners, priv-esc, exfil) is driven
by **Falco** (+ **Tetragon** where the kernel allows). What can actually run is
decided by the node kernel, not by a flag — forcing the wrong probe just
crash-loops the DaemonSet and turns the install into a 10-minute helm timeout.
The installer picks the right tier automatically from the
`keos.pocteo/btf-kernel` label that `keosctl preflight` writes.

## The three tiers

| Tier | When | Falco | Tetragon | Notes |
|---|---|---|---|---|
| **modern_ebpf** | kernel has BTF (`/sys/kernel/btf/vmlinux`) — stock distro kernels ≥5.8, Talos | CO-RE probe, no compilation | ✅ enabled | the happy path |
| **ebpf-prebuilt** | no BTF (NVIDIA L4T/Jetson, some locked-down kernels) | legacy probe from a **prebuilt image** | ❌ (BTF-only) | needs `build-falco-probe.sh` once per kernel |
| **off** | WSL2, or no probe available | — | — | synthetic-event flow still works (`keosctl run lib-*`) |

The chosen tier is recorded in `keosSecurity.tier` (visible in `helm get values`
and `keosctl doctor`).

## Why prebuilt probes (the BTF-less path)

On a BTF-less kernel Falco needs the *legacy* eBPF probe, compiled from the
kernel headers. The in-cluster `falco-driver-loader` **cannot** do this: its
Debian-12 image can't exec the host's newer kbuild tooling (glibc mismatch), so
a feature-test silently fails and the probe miscompiles (full story:
`docs/JETSON_VALIDATION_AND_PORTABILITY.md` §1.4c). The fix is to compile on the
node with its native toolchain and ship the result as a tiny image.

### Build a probe (once per kernel)

On a node with the target kernel (needs `docker`, `clang`, `llvm`, `make`, and
`linux-headers-$(uname -r)`):

```bash
sudo apt-get install -y clang llvm make linux-headers-$(uname -r)
sudo REGISTRY=registry.fleet.local:5000 scripts/build-falco-probe.sh [--insecure]
# → pushes registry.fleet.local:5000/pocteo-platform/falco-probe-<uname-r>:<driver-ver>
```

The installer then wires it automatically: when preflight labelled the node
`keos.pocteo/btf-kernel=false` and `--image-registry` is set, `install-edge.sh`
disables the loader initContainer and adds one that `cp`s the probe from that
image into Falco's `/root/.falco/` (the chart's `extra.initContainers` +
`driver.loader.enabled=false` — no template fork, survives `helm upgrade`).

**Rebuild after a kernel upgrade** — the probe is keyed by `uname -r`.

## Falco → agent delivery

Falco's own `http_output` can't attach an `Authorization` header, and the agent
webhook (`/api/webhooks/falco`) fails **closed** without the bearer token. So
delivery goes through **falcosidekick**, which forwards each alert to
`http://api:8000/api/webhooks/falco` with the header. The installer:

1. generates `FALCO_WEBHOOK_TOKEN`, stores it in the `*-auth` secret (the api
   pod already `envFrom`s it),
2. enables falcosidekick with
   `config.webhook.address` + `config.webhook.customHeaders=Authorization:Bearer <token>`.

Verify end to end: `keosctl doctor --e2e` reads `/etc/shadow` in `nginx-demo`
and asserts a new security incident appears within 60s.

## Tetragon on Jetson

Tetragon hard-requires BTF, which NVIDIA's L4T kernels don't ship. It stays
chart-enabled but pinned to a `keos.pocteo/btf-kernel=true` nodeSelector, so it
schedules **zero** pods on Jetson (helm `--wait` stays green) and lights up
automatically if a BTF-capable node joins. Getting it on Jetson needs a kernel
rebuilt with `CONFIG_DEBUG_INFO_BTF=y`.
