# Where this project sits

Reviewing the Kubernetes AI-agent landscape (CNCF projects + adjacent
commercial tools), every mature option already does one thing well. We
deliberately do *not* compete with any of them head-on. This document is
for a reviewer deciding whether our PoC is duplicative or genuinely
different.

## 1. The landscape

| Axis | **Our PoC** | kagent | K8sGPT | Botkube | Cast AI |
|---|---|---|---|---|---|
| Primary purpose | Edge self-healing on security events | General K8s AI agent platform | Cluster diagnostic translation | ChatOps for K8s notifications | K8s cost optimization |
| Architecture style | LangGraph state machine | Multi-agent + MCP | CLI + rule analyzers | Plugin event router | SaaS + in-cluster agent |
| Action authority | 5 gated tools, OPA matrix | Broad, MCP-mediated | Read-only output | Read + Slack-approved | Mutates (autoscale, bin-pack) |
| Diagnostic source | RAG + Falco + K8sGPT | LLM + MCP tools | Built-in analyzers | Plugin events | Telemetry + ML |
| Policy / guardrails | OPA Rego matrix, RBAC, mode CRD | RBAC + MCP scope | n/a (read-only) | Slack approval flow | SaaS policies |
| Audit | HMAC-signed JSON-lines log | Telemetry / logs | None native | Slack thread history | SaaS change log |
| Deployment shape | Edge K3s + on-host agent | In-cluster pods + UI | CLI or serve container | In-cluster pods | SaaS + cluster agent |
| Best at | Offline edge, narrow safe actions | Broad cloud-native via chat | Surfacing known failure modes | Triage to humans via Slack | Reducing K8s bill |

## 2. What we uniquely bring

Honest read of the table — features no other tool *combines*:

- **Explicit LangGraph state machine over a fixed catalog of 5 actions.**
  Every transition is logged and routable. We don't run a ReAct loop where
  the model can pick a tool freely each turn — the topology is a graph the
  reviewer can read on one page. kagent's MCP-mediated tool selection is
  open-ended by design; ours is closed by design.
- **Severity × action matrix encoded in Rego, not buried in prompts.**
  Operators reason about the policy directly; the LLM cannot talk its way
  past it. No other tool in the comparison ships a declarative matrix file.
- **AgentMode CRD as a kill-switch** (`observe-only | active | frozen`)
  modifiable at runtime via `kubectl patch`, picked up by a watch in under
  one second, with no agent restart. Botkube's Slack-approve flow is the
  closest analogue but is per-action, not a global posture.
- **HMAC-signed audit log over canonical JSON.** Tamper-evident by
  construction. Re-verifiable offline with one function call.
  Kagent/Botkube/Cast log to their respective backends but don't ship
  cryptographic integrity for the audit stream.
- **Designed for ARM64 / Jetson edge.** Quantised 3B local LLM (no GPU
  assumed), K3s as the cluster, no Linux-kernel dependency (Falco is
  emulated by the synthetic-event generator on the laptop, replaced by
  real Falco on the Jetson). Every other tool above assumes a fully-resourced
  cloud cluster.

We deliberately do *not* claim originality on: RAG over runbooks (kagent and
K8sGPT both do this), MCP exposure (everyone is converging on it), or
Pydantic-validated LLM outputs (standard practice).

## 2b. Two agent domains over one graph (Phase 21)

Keos runs **two domains** on the same LangGraph + OPA gate + HMAC audit chain:

- **SRE reliability** — Observe → Predict → Heal → Learn, driven by kubelet /
  Chaos Mesh events on `infra.events.*`.
- **Runtime security** — Detect → Investigate → Contain → Respond, driven by
  **Falco + Tetragon** eBPF telemetry on `security.events.*`.

A single `domain` discriminator (set at triage from the event source) branches
the graph at three nodes — prompt, tool catalog, RAG filter — and nothing else.
One product, two facets; the operator sees both incident streams in one SPA and
one `keosctl`, each badged by domain.

**The security layering is the point.** Keos does not replace
[Tetragon](https://tetragon.io) / [Falco](https://falco.org); it sits *above*
them. Tetragon enforces at the **kernel/syscall layer** (it can `Sigkill` a
malicious process before its syscall completes); Keos adds the
**orchestration layer** — LLM diagnosis over process ancestry + RAG, gated
cross-resource containment (`isolate_pod`, `apply_network_policy`, `kill_pod`),
forensics capture, and human escalation with a signed audit trail. And the
guide's recommended runtime-security rollout — *detection-first, then
enforcement* — **is exactly our observe-only → active posture**. (Design
follows Cilium's
[Kubernetes Runtime Security: The Guide](https://cilium.io/blog/2026/06/07/kubernetes-runtime-security-the-guide/).)

## 3. What we are NOT trying to be

- **Not a replacement for SREs.** We accelerate *known-knowns*; humans own
  the unknowns. Every code path that hits "I don't know" routes to
  `page_human`, not to the LLM's best guess.
- **Not a magic self-healing button.** The action catalog is intentionally
  narrow (5 verbs). Anything outside it escalates by design.
- **Not chat-for-kubectl.** No natural-language query interface. The agent
  consumes Falco events and emits actions; humans drive via `kubectl` and
  the mode CRD.
- **Not the kagent role.** kagent is the full IDE-style assistant with
  Istio/Argo/Helm/Prometheus connectors. We have five tools and one
  policy file.
- **Not the Cast AI role.** Zero cost-optimization signal. Cast AI's
  bin-packing problem is orthogonal to ours.
- **Not the K8sGPT role.** K8sGPT is the diagnostic translator. We *use*
  its findings (Phase 7.2) rather than reimplement them.

## 4. When to choose us vs. kagent / K8sGPT

Pick **kagent** when you have a fully-staffed cluster with cloud LLM
access, want a broad cloud-native tool surface (Istio, Argo, Helm,
Prometheus, Cilium) accessible through chat, and value a polished UI over
a minimal action surface. Pick **K8sGPT** when you want zero-action
diagnostic enrichment of an existing observability pipeline and you'll
handle remediation yourself.

Pick **our shape** when the deployment is offline-capable and resource-
constrained (Jetson-class edge), you need a *policy-gated remediation
loop* (not just diagnosis), you want a tamper-evident audit trail, and you
accept that the action catalog is narrow on purpose. We complement K8sGPT
(we consume its findings) and we are deliberately less ambitious than
kagent — we trade breadth for verifiable safety.

## 5. Roadmap relative to peers

We adopt defensible ideas from peers rather than reinventing them:
**K8sGPT** is wired in as a structured-finding source for the `diagnose`
node (since Phase 7.2), so the agent benefits from K8sGPT's known-failure
catalogue without us reimplementing it. **MCP** is adopted (since Phase
7.3) as the surface for our 5-tool action catalog — any MCP-capable
client (Claude Desktop, kagent, Cursor) can drive remediations under the
same OPA + RBAC + audit guarantees, and the agent's own `execute` node
goes through the MCP boundary to prove the abstraction. **Botkube-style
Slack escalation** is the planned future implementation of our
`page_human` tool, replacing the current stub log line.
