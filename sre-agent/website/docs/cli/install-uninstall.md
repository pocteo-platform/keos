---
id: install-uninstall
slug: /cli/install-uninstall
title: Install + lifecycle
sidebar_position: 2
description: keosctl install · uninstall · upgrade · link — the lifecycle commands that drive Keos onto a cluster and back off.
---

# Install + lifecycle

This page covers the five commands that put the Keos SRE Agent onto a cluster and take it back off: `install`, `uninstall`, `upgrade`, and `link`.

## `keosctl install` — the interactive wizard

The default install path. Runs a 6-step terminal wizard with branded output:

```bash
git clone https://github.com/pocteo-platform/keos
cd keos
bash sre-agent/scripts/keosctl install
```

What happens, step by step:

1. **Prerequisites** — checks `kubectl`, `helm`, `docker`, `openssl`, `python3`, `curl` are on `PATH`. Missing tools are flagged red and the wizard aborts with the install hint.
2. **Cluster** — lists every `kubectl` context with a live reachability probe (3 s timeout per context) and node count. Your `current-context` is pre-selected. You can also paste a kubeconfig path directly.
3. **Discovered facts** — distro (k3s / Talos / Kairos / kubeadm), runtime, target node, GPU capacity, vCPU + memory. Confirm or override the target node.
4. **LLM model** — pick from 4 options sized for the hardware: 1.5B (~0.9 GB, ~2 s/call), **3B (recommended, ~1.9 GB, ~5 s/call)**, 7B (~4.5 GB, ~15 s/call), Llama 3.2 3B.
5. **Image source** — probes GHCR for public visibility, auto-flips to `--local-build` when the packages are private. Operator can override to "public images only" or "air-gapped".
6. **Agent default mode** — `observe-only` (safe default), `active` (with explicit confirm), or `frozen`.

Then a **Review card** lists every chosen value, and on confirm the wizard drives `install-edge.sh` under the hood with all the right flags. Live output relays through the wizard's frame. Final screen: an `✓ Install complete` banner with:

- The port-forward command (`keosctl ui`)
- The bearer token, pre-formatted for paste
- A daily-ops command list
- A symlink of `keosctl` to `~/.local/bin/`

```text
       ┌─────────────────────────────────────────────────────────┐
       │                                                         │
       │      ✓  Install complete                                │
       │                                                         │
       └─────────────────────────────────────────────────────────┘
```

## `keosctl install --headless` — non-interactive

For CI, scripted installs, or when you already know exactly what you want. Skips the wizard and calls `install-edge.sh` directly. **Every install-edge flag passes through.**

```bash
# Common headless invocations
keosctl install --headless --mode observe-only --no-gpu
keosctl install --headless --local-build --mode active
keosctl install --headless --airgap \
                  --image-registry registry.internal:5000 \
                  --local-chart ./sre-agent/deploy/helm/pocteo
```

Headless flags (full reference: `bash sre-agent/scripts/install-edge.sh --help`):

| Flag | Description |
|---|---|
| `--mode <observe-only\|active\|frozen>` | Default agent mode |
| `--kubeconfig <path>` | Explicit kubeconfig |
| `--node <name>` | Pin to a specific node |
| `--namespace <ns>` | Install into a non-default namespace |
| `--release <name>` | Use a non-default Helm release name |
| `--gpu` / `--no-gpu` | Override the GPU auto-detect |
| `--local-build` | Build images locally + import to containerd |
| `--no-local-build-fallback` | Strict: fail if public images aren't reachable |
| `--skip-models` | Skip the LLM model pull |
| `--airgap` | Air-gapped install (requires `--image-registry` + `--local-chart`) |
| `--image-registry <host:port>` | Pull images from a corporate registry instead of `ghcr.io` (works in both airgap and online installs) |
| `--local-chart <path>` | Use a local chart directory |
| `--probe-only` | Dry-run: print what it would do + exit |

#### Corporate registry install (non-airgap)

If your network has internet but you must pull images from a corporate Harbor / Nexus / JFrog (not GHCR), pass `--image-registry` without `--airgap`:

```bash
keosctl install --headless \
  --image-registry registry.corp:5000/pocteo-platform \
  --mode observe-only

# Or the wizard route — Step 5 will surface the option
keosctl install
```

Sets `global.imageRegistry` at install time. You can also swap it later with [`keosctl images set-registry`](./ecosystem-registry#images-set-registry-host).

## `keosctl uninstall` — remove the release

Clean removal with operator confirmation:

```bash
keosctl uninstall
```

By default, this also deletes the PVCs (vector store, audit log, etc.) and the namespace. To keep state:

```bash
keosctl uninstall --keep-pvcs       # Helm release gone, data preserved
keosctl uninstall --keep-namespace  # Helm release gone, ns left alone
keosctl uninstall --keep-pvcs --keep-namespace  # both
```

For automation:

```bash
keosctl uninstall --yes              # skip the confirm prompt
keosctl uninstall --force            # alias for --yes
```

## `keosctl upgrade` — Helm upgrade

Upgrades the running release to a newer chart. By default reads the chart from the cloned repo (`deploy/helm/pocteo`):

```bash
keosctl upgrade
```

To pull a specific chart version from the public OCI registry:

```bash
keosctl upgrade --chart-version 1.0.2
```

Behaviour:
- Uses `--reuse-values` so your existing knobs (mode, GPU, models, etc.) survive
- Uses `--wait --timeout 5m` so the command blocks until pods are Ready
- Prints the last 8 lines of helm output + a success line at the end
- Run `keosctl status` afterwards to verify the new revision

## `keosctl link` — symlink to PATH

Manual re-link (the install wizard does this for you on first run):

```bash
keosctl link                       # default → ~/.local/bin/keosctl
keosctl link /usr/local/bin/keosctl  # explicit target
```

Useful when the original wizard symlink got removed, or when sharing the repo from a different user account.

## Common scenarios

### "I want a clean reinstall on the same cluster"

```bash
keosctl uninstall --force
keosctl install
```

### "I'm setting up CI to install on a fresh cluster every run"

```bash
keosctl install --headless --mode observe-only --no-gpu --skip-models
# (then test, then teardown)
keosctl uninstall --force
```

### "The chart version on GHCR moved; I want to upgrade live"

```bash
keosctl upgrade --chart-version 1.0.2
keosctl status                       # verify pods cycled
keosctl doctor                       # verify health
```

### "I forgot what flags I picked last time"

```bash
helm get values keos -n keos         # the actual values in use
keosctl status --json                # JSON summary
```

## See also

- [`status`, `doctor`, `mode`, `logs`, `ui`, `token`](./operations) — what you'll run after install
- [Air-gapped install guide](../platforms/airgap.md)
- [The headless install-edge.sh reference](https://github.com/pocteo-platform/keos/blob/main/sre-agent/scripts/install-edge.sh)
