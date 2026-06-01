---
id: environment-variables
slug: /configuration/environment-variables
title: Environment variables
sidebar_position: 1
---

# Keos SRE Agent — configuration reference

Every tunable in one place. Source of truth: `agent/config.py` for the
runtime, `deploy/helm/pocteo/values.yaml` for the cluster install.

## Runtime env vars

Read by `agent/config.py`. Set via Helm secret + env on the api Pod, or
exported in the shell for host-side `python -m agent.serve`.

| Name | Default | Used by | Purpose |
|---|---|---|---|
| `SHP_API_TOKEN` | _required_ | api, mcp-server | Bearer token for SPA + API + WebSocket |
| `AUDIT_HMAC_KEY` | _required_ | agent.audit | HMAC-SHA256 key for every audit line |
| `OLLAMA_HOST` | `http://localhost:11434` | agent.llm | LLM inference endpoint |
| `QDRANT_URL` | `http://localhost:6333` | agent.tools, agent.seed_rag | RAG vector store |
| `NATS_URL` | `nats://localhost:4222` | agent.serve, agent.synthetic_events | Event bus |
| `OPA_URL` | `http://localhost:8181` | agent.policy_gate | Policy gate |
| `MCP_URL` | `http://localhost:8090/mcp` | agent.graph | MCP server endpoint |
| `K8SGPT_URL` | `http://localhost:8089/mcp` | agent.k8sgpt_client | Diagnostic-signals source (optional) |
| `KUBECONFIG` | `<repo>/.kube/config` | agent.tools, agent.mode_store | Kubernetes API access |
| `OLLAMA_CHAT_MODEL` | `qwen2.5:3b-instruct-q4_K_M` | agent.llm | LLM model name |
| `OLLAMA_EMBED_MODEL` | `nomic-embed-text` | agent.seed_rag | Embedding model |
| `AGENT_CONFIDENCE_THRESHOLD` | `0.6` | agent.graph | Below this, the graph escalates without planning |
| `AGENT_MODE_NAME` | `default` | agent.mode_store | Name of the singleton AgentMode CR |
| `AUDIT_LOG_PATH` | `<repo>/agent/audit.log` | agent.audit | Where the signed log lives |
| `FALLBACK_AGENT_MODE` | `observe-only` | mcp-server, api | Safe default when mode_store can't read the CR |
| `POCTEO_PRODUCT_NAME` | `Keos SRE Agent` | api (OpenAPI title only) | UI brand override (env name kept for backward-compat) |
| `MCP_BYPASS` | _(unset)_ | agent.graph tests | If set, execute calls agent.tools directly instead of MCP |
| `LOG_LEVEL` | `INFO` | all | Python logger level |
| `QDRANT_COLLECTION` | `runbooks` | agent.seed_rag, agent.tools | Vector collection name |
| `SLACK_WEBHOOK_URL` | _(unset)_ | agent.tools (`page_human`) | Incoming-webhook URL; receives a Block Kit card on every `page_human` call |
| `PAGE_HUMAN_WEBHOOK_URL` | _(unset)_ | agent.tools (`page_human`) | Generic JSON POST endpoint for non-Slack integrations (Teams, custom, PagerDuty Events API) |
| `ALERTMANAGER_WEBHOOK_TOKEN` | _(unset)_ | api (`/api/webhooks/alertmanager`) | Shared secret Alertmanager sends in `Authorization`. Endpoint returns 503 until set. |
| `WATCH_NAMESPACES` | _(unset → all non-system)_ | agent.chaos_observer | Comma-separated namespace allowlist. Empty = watch every namespace except `kube-system`, `kube-public`, `kube-node-lease`, `chaos-mesh`, `local-path-storage`. |

## Helm values.yaml keys

Documented inline in `deploy/helm/pocteo/values.yaml`. Highlights:

| Key | Default | Effect |
|---|---|---|
| `auth.apiToken` | `""` (required) | Maps to `SHP_API_TOKEN` |
| `auth.hmacKey` | `""` (required) | Maps to `AUDIT_HMAC_KEY` |
| `agent.defaultMode` | `observe-only` | Initial AgentMode CR |
| `agent.confidenceThreshold` | `0.6` | Maps to `AGENT_CONFIDENCE_THRESHOLD` |
| `ollama.chatModel` | `qwen2.5:3b-instruct-q4_K_M` | Maps to `OLLAMA_CHAT_MODEL` |
| `ollama.embedModel` | `nomic-embed-text` | Maps to `OLLAMA_EMBED_MODEL` |
| `ollama.persistence.size` | `10Gi` | PVC for model weights |
| `ollama.gpu.enabled` | `false` | Enable NVIDIA GPU offload. See [JETSON.md](/docs/platforms/jetson). |
| `ollama.gpu.resourceName` | `nvidia.com/gpu` | Kubelet resource name. Override for AMD / Intel GPUs. |
| `ollama.gpu.count` | `1` | Number of GPUs to request. Ollama uses 1 even if more are available. |
| `ollama.gpu.runtimeClassName` | `nvidia` | K8s `RuntimeClass` selecting the NVIDIA container runtime. |
| `ollama.gpu.tolerations` | `[]` | Tolerations for GPU-tainted nodes. |
| `ollama.gpu.nodeSelector` | `{}` | Pin Ollama to a specific GPU node when the cluster is mixed. |
| `qdrant.persistence.size` | `2Gi` | PVC for RAG vectors |
| `nats.persistence.size` | `2Gi` | PVC for JetStream durable storage |
| `chaos-mesh.enabled` | `true` | Toggle Chaos Mesh sub-chart |
| `chaos-mesh.chaosDaemon.runtime` | `containerd` | Set to match the node runtime |
| `chaos-mesh.chaosDaemon.socketPath` | `/run/containerd/containerd.sock` | Talos / k3s differ |
| `ingress.enabled` | `false` | Expose Caddy via IngressClass |
| `ingress.host` | `pocteo.local` | DNS name |
| `ingress.tls.enabled` | `false` | Cert-manager handoff |
| `global.imageRegistry` | `ghcr.io/your-org` | Image registry prefix |

## SPA configuration

The SPA is a static build. The bearer token comes from the user pasting
it on first load; no other config is bundled. Brand customization lives
in `frontend/src/lib/branding.ts` — change once, rebuild.

## Auto-generation note

A future iteration will auto-generate this doc from the source files via
`scripts/gen_config_docs.py`. Until then, treat this file as a curated
mirror — keep it updated when adding env vars to `agent/config.py` or
keys to `values.yaml`.
