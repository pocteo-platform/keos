# Demo script — live walkthrough

Read this aloud while running `make demo` on screen. Times are rough; the
real `make demo` takes about 3-4 minutes once the LLM is cached.

Three demos are documented here:

- **Demo A** (`make demo`) — the classic two-mode walkthrough on scenario 1.
  3-4 minutes, suitable for a short executive demo.
- **Demo B** (`python -m agent.demo --scenario 6 --single`) — the K8sGPT
  enrichment showcase. 2-3 minutes once the LLM is cached. Best for a
  technical-depth conversation.
- **Demo C** (`make demo-ui`) — the full web UI tour. 5-6 minutes, best
  for a product-style demo or a stakeholder reviewing the project for
  the first time.

---

# Demo A — scenario 1, observe-only -> active

## Setup (30 s — say while preflight prints)

> "This is a proof of concept for a self-healing Kubernetes edge security
> agent. The eventual target is a Jetson Orin Nano running K3s — security
> events stay on the box, no cloud LLM, no data leaves the host. Right now
> we're running on a laptop with the same architecture: a local Ollama
> model, a vector store of runbooks, K8sGPT for structured cluster
> findings, a real Kubernetes cluster via k3d, an Open Policy Agent
> enforcing what the agent is allowed to do, and an MCP server exposing
> the same 5 action tools to any MCP-capable client."

> "The demo will simulate a Falco-style alert and walk through one full
> incident-response cycle, twice — first in `observe-only` mode where
> the agent diagnoses but cannot act, then in `active` mode where it
> actually heals the cluster."

When preflight finishes, point at the four OK lines:

> "We just confirmed: the LLM and embedding model are loaded, the runbook
> RAG store has its content, OPA's policy is in place, and the cluster's
> AgentMode CRD is reachable. If anything were missing, the demo would
> stop here and tell you the exact command to run."

---

## Step 1: observe-only run (60-90 s)

When "Step 1 -- observe-only" prints:

> "We've just patched the AgentMode CRD to `observe-only`. The agent
> watches it via a Kubernetes watch — no polling — and picks up the
> change on its next decision."

> "Now we publish a synthetic Falco event: a shell was spawned inside the
> nginx container. In a real deployment this would come from Falco
> running on the node; in the PoC we generate it directly. The target
> pod name was looked up from the live cluster — just like real Falco
> would emit it."

While the LLM thinks (~60 s on CPU):

> "What's happening now is a chain of nodes in a LangGraph state machine.
> Triage parses the Falco event into severity and target. Retrieve
> embeds the alert and queries the runbook collection in Qdrant. The
> k8sgpt_analyze node calls K8sGPT — a CNCF tool — over MCP to get
> structured findings about the live cluster state. Then diagnose sends
> the alert plus the runbook hits plus K8sGPT's findings to Qwen2.5-3B
> — a 3-billion-parameter model, 1.9 GB on disk — and asks for
> structured JSON: summary, root cause, confidence, evidence, and an
> optional reconciliation note for when the sources disagree."

> "Every LLM response is validated against a Pydantic schema. If Qwen
> produces malformed JSON, the agent retries once with a 'your last
> response was invalid' hint, and escalates if that fails too."

When the summary table prints:

> "The model came back with 85% confidence — well above our 0.6
> threshold. It proposed `restart_pod`, which makes sense: a fresh
> replica from the trusted image kills any in-process shell."

> "Then policy_gate. OPA evaluates `(action=restart_pod, severity=medium,
> agent_mode=observe-only, target.namespace=workloads)`. Result:
> **DENY**. The matrix would allow restart_pod at medium severity, but
> the global mode gate overrides — observe-only means no action, no
> exceptions."

> "So the graph routes to escalate. The agent pages a human, writes one
> more audit line, and the run ends with `escalated: true`. The cluster
> was not touched."

---

## Step 2: active run (60-90 s)

When "Step 2 -- active" prints:

> "Now we patch AgentMode to `active`. The mode_store watch sees the
> change instantly — no restart needed."

> "Same scenario, same diagnose call — the model is warm, finishes
> faster — same proposed action. But this time the policy gate ALLOWS."

When the summary table prints:

> "Two important things happen here. First, the agent does NOT call
> kubectl directly. Its execute node makes an HTTP call to our MCP
> server, which is the same server Claude Desktop or kagent would use.
> The MCP server runs its OWN OPA check before doing anything —
> defense-in-depth. Both checks see active mode, both allow, kubectl
> deletes the pod, the Deployment respawns a fresh replica."

> "The verify node polls the Deployment until all 3 replicas are Ready
> — or 30 seconds, whichever comes first. Here it confirms 3/3 ready,
> so `healed=true`. No escalation."

---

## Step 3: the audit trail (15 s)

When the audit tail prints:

> "Every single state transition in both runs is in this log. Each line
> is HMAC-signed — change one character and the verifier rejects it.
> Look at the active run — you'll see TWO execute events. One has
> `source: agent`, written by the agent's own audit logger; the next
> has `source: mcp`, written independently by the MCP server. Same
> action, two witnesses."

> "Notice the two `complete` events at the bottom: the observe-only run
> ended with `escalated=True healed=False`, the active run with
> `escalated=False healed=True`."

> "On the Jetson target this audit log will be the canonical record of
> what the agent did and why. Operators can replay any incident from
> the log without re-running the LLM. And because the MCP server emits
> records with `source: mcp`, an operator-driven action via Claude
> Desktop sits in the same forensic stream as an agent-driven one."

---

## Closing (15 s)

> "Summary of what we just demonstrated:"

> "(1) Real RAG over local runbook content. (2) K8sGPT enrichment — the
> LLM never has to invent facts about the cluster. (3) Local LLM
> diagnosis with structured-output validation. (4) Policy-gated
> execution through an MCP server that's reusable by Claude Desktop and
> any other MCP client. (5) Scoped RBAC — the agent literally cannot
> touch kube-system. (6) Live mode-switching for safe rollout.
> (7) Tamper-evident audit with provenance. All on a laptop, all
> offline-capable."

> "Next step before Jetson hardware: scale-test the LLM on a Jetson Orin
> Nano dev kit — Qwen2.5-3B should comfortably hit ~25-40 tokens/sec
> on the Jetson's 8-core ARM with the GPU enabled. That brings
> per-event latency under 10 seconds in production."

---

# Demo B — scenario 6, K8sGPT enrichment

Run with `python -m agent.demo --scenario 6 --single`. Best when the
audience is technical and you want to make the "three diagnostic sources"
story concrete.

## Setup (30 s — say while preflight prints)

> "This second demo isolates one specific design choice. The diagnose
> node in our agent sees up to three independent inputs: the Falco
> alert, top runbook hits from RAG, and structured findings from
> K8sGPT. For scenarios 1 through 5, Falco alone is enough — the rule
> name tells you what happened. Scenario 6 is different: it's a
> deliberately vague Falco event that ANY runtime monitor would emit:
> 'container failed to start in workloads/broken-image'. Falco can't
> tell you why."

When preflight finishes:

> "We're going straight to active mode for this one — the safety net
> story is the same as demo A, we don't need to repeat it. The point
> here is what K8sGPT adds."

---

## The K8sGPT call (15 s — say while the audit shows `k8sgpt_analyze`)

> "The agent just called K8sGPT over the Model Context Protocol — same
> protocol Claude Desktop speaks to our MCP server. K8sGPT runs its
> rule-based Pod and Deployment analyzers against the live cluster and
> returns structured findings. We're not using K8sGPT's own LLM
> summarization — we want raw observations, not a second model in the
> loop."

---

## The diagnose call (60 s — say while the LLM thinks)

When the LLM finishes and the summary table prints, point at the
`k8sgpt_findings` line:

> "Two findings: the broken-image Deployment has 0 of 1 replicas
> available, AND there's a Pod with 'failed to pull and unpack image
> ghcr.io/example/does-not-exist:v999: manifest unknown'. The Falco
> event never mentioned the image — K8sGPT found it from the live API
> server."

Point at `root_cause`:

> "The model picked up the exact image reference and the failure mode
> from the K8sGPT finding. Without that input, the best it could have
> done with the Falco event was guess."

---

## The policy outcome (15 s — variable)

If the LLM picked `rollout_undo` (the runbook-recommended action) and
OPA allowed:

> "Rollout_undo at medium severity is L2 — allowed. The agent reverts
> the Deployment to the previous ReplicaSet whose image was pullable.
> Healed."

If the LLM picked `isolate_pod` (a common 3B-model mistake here):

> "The model overreached — isolate_pod doesn't fix a pull failure. But
> the policy gate caught it: isolate_pod at medium severity is L3,
> denied. The agent escalates to a human. This is exactly the safety
> net we designed for — the model is allowed to be wrong; the policy
> isn't."

---

## Closing (15 s)

> "The pipeline is intentionally designed so the LLM never has to
> invent facts about the cluster. K8sGPT is rule-based, deterministic,
> reads the live API server. The 3B model is good at choosing an
> action when handed pre-digested facts; it's bad at reasoning about
> raw cluster state. Splitting the labour matches each component to
> what it's good at."

> "And when the model still makes a poor choice, OPA catches it. Two
> safety nets, both visible in the audit log."


---

# Demo C — Web UI walkthrough

Run with `make demo-ui`. This brings up the entire stack (compose + the new
API/frontend/Caddy services), opens `http://localhost` in your default
browser, and auto-dispatches scenario 1 about 5 seconds in so you land in
the middle of a live pipeline animation.

## Setup (60 s — say while the stack builds)

> "What you'll see for the next two minutes is the same agent we walked
> through in the CLI demo, but wrapped in a single-page web app. Same
> OPA gate underneath, same MCP server doing the actions, same HMAC
> audit log. The UI is a thin layer that talks only to a local FastAPI
> endpoint on the same machine. No data leaves the laptop."

When the browser opens and the paste-token screen appears:

> "The SPA has no baked credentials. The bearer token was generated
> during make up and written to .env.local at the repo root. I'm
> pasting it here. From now on every API call from the SPA carries
> this token, and OPA enforces the same matrix it does for the agent
> and the MCP server."

After the cluster view loads:

> "Cluster view. Four stat cards at the top — nodes, namespaces,
> running pods, current agent mode. Below, the workloads namespace
> with our nginx-demo pods. Watch one of them: when the agent
> restarts it in a moment, the card will flash cyan and the running
> count won't change because the Deployment respawns immediately."

## Live pipeline (60–90 s)

Navigate to Pipeline → Live. Scenario 1 should fire automatically:

> "This is the LangGraph state machine, rendered live. The pulsing
> cyan node is the one the agent is in right now. As nodes finish
> they turn green and elapsed-time chips show how long each step
> took. The retrieve_context, k8sgpt_analyze, and diagnose nodes
> hand off via straight edges; the policy_gate has a conditional
> branch to the escalate sink in amber."

When diagnose finishes (~60s on CPU):

> "Click the diagnose node — the side drawer shows you the validated
> JSON output the LLM produced. Confidence, summary, root cause,
> evidence, and the optional reconciliation note we added in Phase
> 7.2 for when K8sGPT and Falco disagree. Same payload that's in
> the audit log; the SPA never invents anything."

After policy_gate (in observe-only mode it denies → escalate):

> "Policy gate denied because the agent is in observe-only. Now
> let's flip it."

## Control + active run (45 s)

Navigate to Control:

> "Three modes — observe-only, active, frozen. Click Active, confirm.
> The mode_store watch in the agent picks this up within a second."

> "Audit log at the bottom — every action the agent has taken is
> here, HMAC-signed. Notice the source column: events written by the
> agent's own logger are tagged 'agent', events written by the MCP
> server appear as 'mcp'. Same incident, two witnesses."

Run scenario 1 again (Pipeline → Live, or via the Incidents detail page):

> "Same scenario, active mode now. Watch the policy_gate node —
> green this time. Execute fires, kubectl deletes the offending pod,
> verify confirms the Deployment respawned a fresh replica.
> Healed=true, no escalation."

## Incidents + audit chain (30 s)

Navigate to Incidents, click into the most recent one:

> "Eight tabs per incident. Pipeline replay, raw Falco event, RAG
> hits, K8sGPT findings, the LLM's structured responses with token
> timing, the OPA decision with reasons, the action result, and
> finally the audit chain — every record we wrote for this
> incident, HMAC-verified inline. The green Chain valid badge
> tells you nothing was tampered with after the fact."

## Use Cases (60 s)

Navigate to Use Cases:

> "Three library use cases come pre-seeded — crypto-miner, privileged
> container, suspicious egress. Both English and French descriptions.
> Click New Use Case to build your own."

Walk through the wizard briefly:

> "Six steps: context (name + descriptions), target (which pod), the
> Falco template (live JSON preview on the right), expected outcomes
> (these are evaluated automatically after the run), optional runbook
> (embedded into Qdrant at run time, tagged with the case id), and
> review."

> "Save and run drops the user on /pipeline/<id>?usecase=<id> — same
> animation as before, plus a banner at the top comparing
> expectations to actuals with green checks or red X. This is how
> a prospect would build a scenario for their own threat model and
> verify the agent handles it."

## Closing (15 s)

> "What you saw: the same agent, exposed via a real product surface.
> Single page, bilingual, zero data leaving the box, OPA in force on
> every button, audit trail provable cryptographically. Built on the
> standard FastAPI + React + shadcn stack, no proprietary anything,
> deployable as five docker-compose services. The next phase is
> running this exact build on a Jetson Orin Nano and replacing the
> synthetic Falco generator with real Falco on the node."

