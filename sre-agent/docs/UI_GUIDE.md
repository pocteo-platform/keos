# Web UI — Screenshot tour

The web UI ships with five views, all reachable from the left sidebar.
This file is a tour. Run `make demo-ui` to follow along.

> Screenshots are placeholders for now (the PoC ships without baked
> images). Generate fresh ones via your screenshot tool of choice once
> the stack is up; the layout has been locked since Phase 8.6.

## 1. Login (`/login`)

The SPA never bakes a token. On first load it prompts for the
`SHP_API_TOKEN` value stored in `.env.local` at the repo root.

```
make up                     # generates .env.local on first run
cat .env.local              # or `Get-Content .env.local` on PowerShell
```

Paste the value (with or without the `SHP_API_TOKEN=` prefix) and click
**Sign in**. The token persists in `localStorage` so reloads stay
authenticated. The **Logout** button in the top bar clears it.

![login](placeholder-login.png)

## 2. Cluster (`/cluster`)

Four stat cards (Nodes / Namespaces / Running pods / Agent mode) sit
above a namespace picker. The pod grid below refreshes every 4 seconds
via React Query AND on the `cluster` WebSocket channel; any pod whose
phase or restart count changes flashes with a cyan ring for 2 seconds.

Click any pod card to open a side drawer with its phase, ready count,
restart count, age, and last-event preview.

![cluster](placeholder-cluster.png)

## 3. Incidents (`/incidents`)

Filterable + sortable + paginated table. Every filter (search,
severity, namespace, status) is reflected in the URL query string, so
links you share with a colleague restore the same view. Click any row
to drill into the detail page.

![incidents-list](placeholder-incidents-list.png)

### Detail (`/incidents/:id`)

A header card summarises the incident (id, time, severity, outcome,
mode at time of incident). Below it, an 8-tab Tabs container:

| Tab | What's in it |
|---|---|
| **Pipeline** | Static, final-state React Flow canvas of the LangGraph run. |
| **Falco event** | The raw incoming event JSON. |
| **RAG context** | The top runbook hits Qdrant returned, with similarity scores. |
| **K8sGPT findings** | Structured findings from K8sGPT, if any. |
| **LLM responses** | Validated diagnosis + plan_action JSON with elapsed-ms chips from the pipeline trace. |
| **Policy decision** | OPA's decision + reasons + raw body. |
| **Action result** | Tool dispatched + stdout/stderr/error blocks + raw `ActionResult` object. |
| **Audit chain** | Per-incident audit records with HMAC chips; per-row drawer; chain-validity badge. |

![incidents-detail](placeholder-incident-detail.png)

## 4. Pipeline (`/pipeline/live`, `/pipeline/:id`)

The wow-factor view.

- **Live** (`/pipeline/live`): subscribes to the WS `pipeline.*` wildcard.
  As soon as an incident starts, the canvas locks onto it; subsequent
  start/end events animate the 9 nodes from pending → active → done.
  A pulsing `● LIVE` badge sits in the top-right.

- **Replay** (`/pipeline/:id`): fetches the incident's stored trace
  records and replays them at user-controlled speed. The scrubber at
  the bottom supports play/pause/step + 0.5× / 1× / 2× / 4× / instant.
  Click any node mid-replay to open a side drawer with the input +
  output JSON snapshots from that node's start/end events.

If the URL contains `?usecase=<use-case-id>`, a **Results banner** at
the top of the replay view compares the use case's expectations
(diagnosis keywords, proposed tool, policy decision, outcome) against
the actual incident with ✓/✗/? chips.

![pipeline](placeholder-pipeline.png)

## 5. Use Cases (`/usecases`)

Card grid of saved scenarios. A library starter-set of three
(crypto-miner, privileged-container, suspicious-egress) is seeded on
first launch, EN + FR descriptions included. Each card has four icon
buttons:

| Button | What it does |
|---|---|
| **Duplicate** | Clones into the wizard with a fresh id and "(copy)" suffix. |
| **Edit** | Opens the 6-step wizard prefilled with the use case. |
| **Delete** | Confirms via browser prompt, then removes. |
| **Run** | Publishes the rendered Falco event to NATS and navigates to `/pipeline/<incident_id>?usecase=<id>`. |

![usecases](placeholder-usecases.png)

### Wizard

Six steps with a horizontal step indicator. Active step is highlighted;
completed steps are clickable to jump back; forward jumps are locked
behind step-level validation.

1. **Context** — name, EN + FR descriptions, free-form tag chips.
2. **Target** — namespace + owner kind dropdowns + segmented
   specific-pod / by-label toggle. Specific mode lists running pods.
3. **Falco event** — rule, priority, output template with
   `{pod}/{ns}/{image}` placeholders + key/value output_fields editor.
   Live JSON preview on the right.
4. **Expected** — diagnosis keywords (substring match), proposed tool,
   policy decision, outcome. All optional. Evaluated on the results
   banner after a run.
5. **Runbook** — optional EN + FR markdown. When present, the runbook
   is embedded into Qdrant just before the case runs, tagged with the
   use-case id.
6. **Review** — summary + raw JSON + Save / Save & run.

![wizard](placeholder-wizard.png)

## 6. Control (`/control`)

- **Mode toggle**: segmented control with confirm dialog. Patches the
  `AgentMode` CRD; the watch in the agent picks the change up in <1s.
- **Pending approvals**: any L3 matrix-denied action that landed in the
  pending queue (Phase 8.1's `approvals.py`). Approve dispatches it
  through MCP with `severity=critical` and a `human_override=true`
  audit marker. Deny records the reason in audit.
- **Audit log**: paginated, searchable. Source column distinguishes
  `agent`-written records from `mcp`-written records. Click any row
  for the full JSON + HMAC verification chip.

![control](placeholder-control.png)

## Demo flow

```powershell
.\make.ps1 demo-ui
# 1. waits for the stack to come up
# 2. opens http://localhost in your browser
# 3. paste the SHP_API_TOKEN from .env.local on the login screen
# 4. 5 seconds later, scenario 1 is dispatched automatically
# 5. switch to /pipeline/live to watch the agent reason in real time
# 6. flip to /control, set the mode to active, re-run scenario 1
# 7. drill into the incident from /incidents -> any of the 8 tabs
```

Total demo time on a model-cached machine: about 4 minutes.
