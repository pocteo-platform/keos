---
id: quickstart
slug: /getting-started/quickstart
title: Quickstart
sidebar_position: 3
description: Three commands, ten minutes, first scenario running.
---

# Quickstart

The fastest path from zero to a working agent. Assumes Docker Desktop
is running and you've handled the [Windows one-time setup](installation#windows-one-time-setup)
if applicable.

## The three commands

```bash
git clone https://github.com/pocteo-platform/keos
cd keos/sre-agent
make install         # Windows: .\make.ps1 install
make demo-ui         # opens the SPA in your default browser
```

On a cold machine that's about **10 minutes**, mostly the LLM model
download. Subsequent `install` runs skip the cached steps.

## What just happened

1. `git clone` pulled the repo. The agent codebase lives in `sre-agent/`.
2. `make install`:
   - Auto-installed `uv`, `k3d`, `opa`, `helm` to `~/.local/bin`
   - Generated a random bearer token into `.env.local`
   - Pulled `qwen2.5:3b` (~1.9 GB) and `nomic-embed-text` (~270 MB) into Ollama
   - Started 9 containers via docker compose
   - Created a single-node k3d cluster + applied demo workloads
   - Indexed 8 SRE runbooks into Qdrant
   - Ran `doctor` and printed the green-light report
3. `make demo-ui` opened **http://localhost** and started a `lib-memory-leak`
   scenario after 5 seconds.

## The first thing you'll see

The SPA prompts for a bearer token. Get it from `.env.local`:

```bash
cat .env.local          # macOS / Linux / WSL
```

```powershell
Get-Content .env.local  # Windows
```

Paste the value after `SHP_API_TOKEN=` into the SPA. The token persists
in browser localStorage; you won't be asked again.

## Watch the loop

The browser opens on `/pipeline/live` — the live timeline. Within a few
seconds you'll see the four phases populate top-down as each LangGraph
node fires:

| Phase | Nodes you'll see |
|---|---|
| 🟧 **Observe** | `triage` → `retrieve_context` → `k8sgpt_analyze` |
| 🟧 **Predict** | `diagnose` → `plan_action` |
| 🟦 **Heal** | `policy_gate` → `execute` |
| 🟩 **Learn** | `verify` |

A first scenario takes **4–5 minutes on CPU** (most of it spent in the
two LLM calls — diagnose + plan_action). Subsequent runs are similar
because the model stays resident for 30 minutes.

When the graph completes, the **Telemetry sidebar** shows pods-ready,
restarts, actions taken. Each card in the timeline shows the node's
input/output snapshot — click any node to open the drawer.

## Try something different

Once the first scenario is done:

1. Go to **Use Cases** in the sidebar.
2. Pick `lib-crashloop-recovery` or `lib-node-pressure`.
3. Click **Run**.

Same loop, different rule, different RAG hits, different proposed action.
The next scenario is fast (~30 s) because the model is warm.

## Mode flip

By default the agent is in `observe-only` mode — the loop runs end-to-end
but every action is dry-run (logged, not executed). To let it act on
the cluster:

1. Go to **Control** in the sidebar.
2. Flip the agent mode from `observe-only` → `active`.
3. Re-run a scenario. Now `execute` actually calls kubectl through
   the MCP server.

The `frozen` mode is the kill switch — OPA denies every action while
in `frozen`, regardless of severity.

## What if it didn't work

```bash
make doctor          # Linux / macOS / WSL
.\make.ps1 doctor    # Windows
```

Every component reports `[OK]` or `[--]` with a fix command. If `doctor`
is fully green but the UI is still misbehaving, see [Runbooks](../runbooks/)
for the symptom-to-runbook decision tree.

Next: [Your first scenario, in depth](your-first-scenario).
