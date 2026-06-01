---
id: claude-desktop
slug: /operations/claude-desktop
title: Claude Desktop integration
sidebar_position: 4
---

# Using the MCP server from Claude Desktop

The self-healing MCP server exposes 5 tools (`restart_pod`, `rollout_undo`,
`isolate_pod`, `apply_network_policy`, `page_human`). When wired into
Claude Desktop, you can ask Claude to take cluster actions; every call
flows through the **same OPA policy** as the agent's automated path. The
mode CRD (`observe-only / active / frozen`) gates Claude's actions
identically to the agent's.

## Prerequisites

1. The PoC stack is up (`.\make.ps1 up && .\make.ps1 cluster`).
2. Either:
   - the MCP server container is running (`docker compose ps mcp-server`
     shows it Up) — recommended for HTTP transport, OR
   - you have the repo on your host and can run `python -m mcp_server` —
     required for stdio transport (Claude Desktop's default).
3. `AgentMode` is set to `active` (otherwise OPA denies every call).
   Flip with:
   ```powershell
   kubectl --kubeconfig .\.kube\config patch agentmode default `
     --type=merge --patch-file manifests\patches\mode-active.yaml
   ```

## Configuring Claude Desktop (stdio transport)

Claude Desktop's config file lives at:

- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`
- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`

Add an entry under `mcpServers`. Adjust the absolute paths to match your
repo location:

```json
{
  "mcpServers": {
    "self-healing-actions": {
      "command": "python",
      "args": ["-m", "mcp_server", "--transport", "stdio", "--no-mode-watch"],
      "cwd": "A:\\argentic\\DEV\\self-healing-poc",
      "env": {
        "OPA_URL": "http://localhost:8181",
        "KUBECONFIG": "A:\\argentic\\DEV\\self-healing-poc\\.kube\\config",
        "AUDIT_LOG_PATH": "A:\\argentic\\DEV\\self-healing-poc\\agent\\audit.log",
        "AUDIT_HMAC_KEY_FILE": "%LOCALAPPDATA%\\self-healing-poc\\hmac.key",
        "FALLBACK_AGENT_MODE": "observe-only"
      }
    }
  }
}
```

**Notes:**

- `--no-mode-watch` is used because Claude Desktop spawns a fresh process
  per session; starting a Kubernetes watch every spawn is wasteful. The
  server falls back to `FALLBACK_AGENT_MODE` if the mode CRD can't be
  read — keep it `observe-only` so an unconfigured deploy fails closed.
  Remove the flag if you want live mode-flips inside a single Claude
  session.
- **Never put the HMAC key in this file.** The example references an env
  var (`AUDIT_HMAC_KEY_FILE`) — set it to a path containing the secret,
  managed by your OS keystore. If you absolutely must inline a key for a
  laptop PoC, use the `.env` file in the repo (gitignored) and remove the
  `AUDIT_HMAC_KEY_FILE` entry above — the server will fall through to
  `agent.config.settings.audit_hmac_key`.
- Claude Desktop runs `python` from your user PATH. If `python` resolves
  to the wrong interpreter, either specify `command: "C:\\path\\to\\python.exe"`
  or wrap with `uv run`.

Restart Claude Desktop after editing the config. The 5 tools will appear
under the wrench/tools menu in a new chat.

## Configuring with HTTP transport (long-running)

If the MCP server is running in compose (`docker compose ps mcp-server`),
clients that prefer HTTP can connect directly:

```
http://localhost:8090/mcp
```

This is what `agent/graph.py` does internally. Claude Desktop currently
supports stdio MCP only; other clients (kagent, custom orchestrators) can
use the HTTP path.

## Verifying the wiring

In a new Claude Desktop session, try:

> "List the pods in the workloads namespace."

Claude doesn't yet have a `get_pods` tool in our catalogue (we
deliberately ship only the 5 actions), so it will ask you for a
remediation instead. Try:

> "Restart the pod nginx-demo-xyz in the workloads namespace."

Claude will call `restart_pod`. If `AgentMode` is `active`, the pod is
deleted and respawned. If `observe-only`, OPA denies and Claude reports
the denial reason. Check `agent/audit.log` afterwards — the new record
has `"source": "mcp"`.

## Security guardrails — what stays in force

- OPA's full matrix (severity × action × namespace) gates Claude exactly
  the same as the agent. Claude **cannot** target `kube-system`,
  `kube-public`, or `security`.
- The `AgentMode` CRD is the global kill-switch. Set it to `frozen` and
  every Claude-driven tool call denies.
- Every successful tool call writes an HMAC-signed audit record. The
  source field distinguishes operator-driven (`mcp`) from agent-driven
  (`agent`) actions for forensics.
- Underneath the MCP server, kubectl uses the **scoped ServiceAccount
  token** (`security/self-healing-agent`), which has Role-bound rights
  only in `workloads`.

If a Claude-driven action ever appears to be unsafe, the chain to inspect
is: `mode CRD` → `audit.log entry` → `OPA decision` → `RBAC scope`. The
agent has no special privilege Claude lacks; reasoning about one means
reasoning about both.

> _Screenshot placeholder — once a session has been captured, embed
> `docs/claude-desktop-pod-restart.png` showing the tool-call flow._
