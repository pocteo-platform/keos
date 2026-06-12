---
id: cli-reference
slug: /cli
title: CLI Reference (keosctl)
sidebar_position: 4
description: Complete reference for keosctl — the single command for installing, operating, releasing, and troubleshooting Keos. Every subcommand, grouped by task.
---

# CLI Reference — `keosctl`

`keosctl` is the one tool for the whole lifecycle. Symlink it onto your `PATH`
(`keosctl link`, or `keosctl install` does it) and run `keosctl <command>`.

Every command supports `-h`/`--help` for focused usage. Global flags work
before or after the command:

| Global flag | Default | Meaning |
|---|---|---|
| `-n, --namespace <ns>` | `keos` | release namespace |
| `-r, --release <name>` | `keos` | Helm release name |
| `--kubeconfig <path>` | env | kubeconfig to use |
| `--no-color` | — | disable ANSI colour |

Errors are actionable: each failure says what went wrong and the exact command
to fix it. Exit code is `0` on success, non-zero on failure (`doctor` returns
the failure count).

## Install & lifecycle

| Command | What it does |
|---|---|
| `keosctl install` | Interactive install wizard (cluster · model · images · mode · security). |
| `keosctl install --headless [flags]` | Non-interactive install (CI/scripted); passes flags to the edge installer. |
| `keosctl uninstall [--keep-pvcs] [--keep-namespace] [-y]` | Remove the release (confirms first). |
| `keosctl upgrade [--chart-version V] [--dry-run]` | `helm upgrade --reuse-values`. |
| `keosctl link [path]` | Symlink keosctl onto PATH (default `~/.local/bin`). |
| `keosctl preflight [--gpu] [--image-registry H] [--json]` | Pre-install environment checks with remedies. |
| `keosctl gpu-setup [status\|--apply]` | Wire NVIDIA GPU into containerd (CDI, Tegra-aware). |

## Day-to-day operations

| Command | What it does |
|---|---|
| `keosctl status [--watch] [--json]` | Release + pods + agent mode summary. |
| `keosctl doctor [--e2e]` | Health check across every component (`--e2e` triggers a live security detection). |
| `keosctl top` | Live one-screen dashboard (mode / pods / incidents). |
| `keosctl mode [observe-only\|active\|frozen] [--reason T] [-y]` | Show or set the agent kill-switch. |
| `keosctl ui [--port N] [--no-open]` | Port-forward + open the SPA. |
| `keosctl token` | Print the bearer token (cached at `~/.keos-token`). |
| `keosctl logs <component> [-f]` | Tail a component's logs. |
| `keosctl restart <component> [--wait]` | Rollout-restart (Deployment) or delete (StatefulSet). |
| `keosctl shell <component>` | Interactive shell in a pod. |
| `keosctl describe <component>` | `kubectl describe` a pod. |
| `keosctl port-forward <component> <port>` | Generic port-forward. |

Components: `api · mcp-server · frontend · caddy · ollama · qdrant · nats · opa`.

## Agent & incidents

| Command | What it does |
|---|---|
| `keosctl events [-n N] [-w] [--domain sre\|security] [--filter outcome=X] [--since DUR]` | List incidents (coloured by outcome). |
| `keosctl incidents {list\|get <id>\|delete <id>}` | Inspect / remove an incident. |
| `keosctl usecases list [--domain D]` · `usecases show <slug>` | The scenario library. |
| `keosctl run <slug> [--dry-run]` | Trigger a use case end-to-end. |
| `keosctl audit {tail [-n N]\|verify}` | Tail / verify the HMAC-signed audit chain. |
| `keosctl policy {show\|test <action> <severity> <ns> <mode>}` | Inspect / test the OPA matrix. |
| `keosctl quarantine {list\|release <pod> <ns>}` | List / undo security containment. |
| `keosctl calibration {list\|set <rule> <thr>\|reset [--all]}` | Per-rule confidence thresholds. |

## Data & knowledge

| Command | What it does |
|---|---|
| `keosctl seed [--clear-first]` | Re-seed the runbook corpus into Qdrant. |
| `keosctl models {list\|pull <m>\|rm <m>\|show <m>}` | Manage Ollama models. |
| `keosctl runbooks {list\|show <slug>}` | The corpus the agent retrieves from. |
| `keosctl backup [-o path] [--include-models]` | Tarball audit + vector store + values + CR. |
| `keosctl restore <path>` | Restore from a backup tarball. |
| `keosctl diag [path]` | Generate a support bundle. |

## Build & release (workstation)

These front the build helpers so you never run a raw script. `REGISTRY` is the
private fleet registry; add `--insecure` for a plain-HTTP/self-signed lab one.

| Command | What it does |
|---|---|
| `REGISTRY=<h:5000> keosctl release {build\|mirror\|chart\|config\|verify\|all}` | Publish images + OCI chart (+ GitOps config) to the registry. |
| `keosctl bundle create` | Build the offline air-gap tarball (images + chart + models). |
| `sudo REGISTRY=<h:5000> keosctl falco-probe build [--insecure]` | Build the Falco eBPF probe for this node's kernel. |
| `keosctl images audit` | Multi-arch audit of the pinned images. |

## GitOps (Flux, air-gapped)

| Command | What it does |
|---|---|
| `keosctl gitops bootstrap --registry <h:5000> --age-key <f> --config-tag <t> [--plain-http]` | Install Flux + apply the root reconciler. |
| `keosctl gitops render --registry <h:5000> [--env edge]` | Emit a starter `values-<env>.yaml`. |
| `keosctl gitops {status\|sync\|suspend\|resume}` | Inspect / force / pause reconciliation. |

See [Operations → GitOps](./OPERATIONS.md#gitops-pull-based-delivery).

## Configuration & ecosystem

| Command | What it does |
|---|---|
| `keosctl values {get [k]\|set <k>=<v>\|edit\|diff}` | Read/patch Helm values. |
| `keosctl images {list\|registry\|set-registry <h>\|pull-policy <P>}` | Image refs + registry. |
| `keosctl deps {list\|install <name>}` | Detect/install ecosystem operators (Prometheus, Argo, Flux, cert-manager, Falco, Tetragon). |
| `keosctl chaos {list\|delete <name> [ns]}` | Chaos Mesh experiments. |
| `keosctl bus {tail [subject]\|streams}` | NATS / JetStream introspection. |
| `keosctl config view` · `keosctl context` · `keosctl version` | Effective config / context / versions. |
| `keosctl completion {bash\|zsh\|fish}` | Shell completion script. |
| `keosctl update` | Check for a newer keosctl. |

## Common recipes

```bash
keosctl install                                  # first install
keosctl mode active && keosctl run lib-reverse-shell   # let the agent contain a live threat
keosctl events -w                                # watch incidents stream
keosctl audit verify                             # prove the audit chain is intact
keosctl doctor --e2e                             # confirm the full security path works
REGISTRY=registry.fleet.local:5000 keosctl release all   # populate a private fleet registry
```
