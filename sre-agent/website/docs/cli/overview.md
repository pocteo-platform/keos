---
id: overview
slug: /cli/
title: CLI overview
sidebar_position: 1
description: keosctl is the single CLI that drives the whole Keos SRE Agent lifecycle — install, daily ops, model management, audit, completion.
---

# `keosctl` — the Keos SRE Agent CLI

`keosctl` is the **one tool** an operator needs to drive the Keos SRE Agent end-to-end. It wraps everything that used to be scattered across `kubectl`, `helm`, `ollama` exec calls and bespoke port-forward commands behind a single, consistent interface — with branded output and sensible defaults.

```bash
keosctl install        # interactive 6-step wizard (recommended for first install)
keosctl status         # release + 13-pod table + agent mode
keosctl ui             # port-forward + open the SPA in your browser
keosctl logs api -f    # tail the agent in real time
keosctl mode active    # let the agent take action
keosctl events --watch # stream incidents as they happen
keosctl help           # everything else (5 sections × 23 commands)
```

It follows the `<tool>ctl` convention (think `kubectl`, `talosctl`, `helmctl`, `argocd`), so the muscle memory carries over. Bash 4+ required; ships as a single self-contained script — **no extra deps** beyond what the install already needs.

## Installation

`keosctl` is symlinked automatically by [`keosctl install`](./install-uninstall) into `~/.local/bin/keosctl` on first run. If `~/.local/bin` isn't on your `PATH` yet, the wizard prints the one-line `export` to add.

If you're cloning the repo manually:

```bash
git clone https://github.com/pocteo-platform/keos
cd keos

# Option A — run from the repo path
bash sre-agent/scripts/keosctl status

# Option B — symlink it once, then run as `keosctl …`
bash sre-agent/scripts/keosctl link
keosctl status
```

Verify the install:

```bash
$ keosctl version
keosctl      v1.0.1
Helm chart   pocteo-1.0.1
Repo root    /home/operator/keos/sre-agent
Bash         5.2.21(1)-release
```

## The 23 commands at a glance

| Group | Command | What it does |
|---|---|---|
| **Lifecycle** | [`install`](./install-uninstall) | Interactive 6-step wizard |
| | `install --headless` | Non-interactive (CI / scripted) |
| | `uninstall [--keep-pvcs] [--force]` | Remove the release |
| | `link [path]` | Symlink keosctl onto your `PATH` |
| | `upgrade [--chart-version v]` | Helm upgrade |
| **Operations** | [`status`](./operations) `[--watch] [--json]` | Release + pods + mode summary |
| | `doctor` | Health check across every component |
| | `mode [obs\|active\|frozen]` | Print or set the agent mode |
| | `logs <component> [-f]` | Tail pod logs |
| | `ui [--port N]` | Port-forward + open SPA |
| | `token` | Print the bearer token |
| | `restart <component>` | Rollout-restart a component |
| | `shell <component>` | Interactive bash/sh in a pod |
| | `describe <component>` | `kubectl describe` shortcut |
| | `port-forward <c> <port>` | Generic forward (knows ports) |
| **Agent** | [`events`](./agent) `[-n N] [--watch]` | Recent incidents (colored) |
| | `run <use-case-slug>` | Trigger a use case |
| | `usecases list \| show <slug>` | List or inspect use cases |
| | `audit tail [-n N]` | Tail the HMAC audit log |
| | `audit verify` | Verify the HMAC chain |
| **Data** | [`seed`](./data) | Re-seed the runbook corpus |
| | `models {list\|pull\|rm\|show}` | Manage Ollama LLM models |
| **Shell** | [`completion <bash\|zsh\|fish>`](./shell-completion) | Generate shell completion |
| **Ecosystem + registry** | [`deps list \| install \| verify`](./ecosystem-registry) | Detect or install Prometheus / ArgoCD / Flux / cert-manager / kube-state-metrics |
| | [`images list \| registry \| set-registry \| pull-policy`](./ecosystem-registry) | Per-pod image refs + registry swap |
| | [`values get \| set \| edit \| diff`](./ecosystem-registry) | Helm values inspection + editing |
| **Agent introspection** | [`policy show \| test`](./introspection) | OPA matrix + hypothetical-tuple tester |
| | [`runbooks list \| show`](./introspection) | The corpus the agent retrieves from |
| | [`bus tail \| streams \| info`](./introspection) | NATS event-bus taps |
| **Misc** | `context` | Current kubectl context |
| | `version` | CLI + chart + bash versions |
| | `help` | Banner + categorised command list |

## Global flags

Every command honours the same global flags (they can come **before or after** the subcommand):

| Flag | Default | Description |
|---|---|---|
| `-n`, `--namespace <ns>` | `keos` | Target namespace |
| `-r`, `--release <name>` | `keos` | Helm release name |
| `--kubeconfig <path>` | `$KUBECONFIG` | Override kubeconfig |
| `--no-color` | unset | Disable ANSI colors (CI-friendly) |
| `-h`, `--help` | — | Show help |

```bash
# All equivalent:
keosctl -n my-keos status
keosctl status -n my-keos
KEOS_NAMESPACE=my-keos keosctl status
```

The env vars `KEOS_NAMESPACE` and `KEOS_RELEASE` are honoured as defaults for shell sessions where you operate on a non-standard install.

## Design principles

1. **Single tool, no extra deps.** Pure bash 4+, `kubectl`, `helm` — the same things you already need to run the install.
2. **Branded but parseable.** Pretty output by default (colored tables, banner on `help`); `--json` on `status` for monitoring scripts; `--no-color` for CI logs.
3. **Confirms when it should.** `mode active` and `uninstall` both ask before mutating; both have `--yes` / `--force` for automation.
4. **Talks to the API like the SPA does.** `events`, `run`, `usecases`, `audit` all go through the same `/api/*` endpoints the React UI calls — so anything the SPA can show, the CLI can show too.
5. **Smart fallbacks.** Token is cached at `~/.keos-token` on first read. Unknown commands print "Did you mean…?" with fuzzy suggestions. Pod selectors auto-resolve the right container port.

## Next steps

- [Install + lifecycle](./install-uninstall) — the wizard, headless install, uninstall, upgrade
- [Operations](./operations) — `status`, `doctor`, `mode`, `logs`, `ui`, `token`, `restart`, `shell`, `describe`, `port-forward`
- [Agent](./agent) — `events`, `run`, `usecases`, `audit`
- [Data](./data) — `seed`, `models`
- [Shell completion](./shell-completion) — bash, zsh, fish

Or jump to the full reference: `keosctl help`.
