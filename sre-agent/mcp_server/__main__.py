"""CLI entrypoint for the MCP server.

python -m mcp_server                    # stdio transport (Claude Desktop default)
python -m mcp_server --transport http   # HTTP+SSE on :8090 (agent + kagent + curl)
"""

from __future__ import annotations

import argparse
import sys

from agent.logging_utils import configure_logging
from agent.mode_store import mode_store
from mcp_server.server import mcp


def main() -> int:
    parser = argparse.ArgumentParser(description="self-healing MCP server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default="stdio",
        help="MCP transport. stdio = Claude Desktop spawn; http = long-lived HTTP+SSE server.",
    )
    parser.add_argument(
        "--host", default="0.0.0.0", help="HTTP bind address (http transport only)."
    )
    parser.add_argument(
        "--port", type=int, default=8090, help="HTTP bind port (http transport only)."
    )
    parser.add_argument(
        "--no-mode-watch",
        action="store_true",
        help="Skip starting the AgentMode CRD watcher (useful when "
        "the server runs without cluster access; OPA will fall back "
        "to FALLBACK_AGENT_MODE).",
    )
    args = parser.parse_args()

    configure_logging()

    # The MCP server reads the same AgentMode CRD as the agent. We start the
    # watch lazily; if there is no cluster, --no-mode-watch keeps the server
    # functional with FALLBACK_AGENT_MODE=observe-only as the safe default.
    if not args.no_mode_watch:
        try:
            mode_store.start()
        except Exception:  # noqa: BLE001
            pass

    if args.transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(transport="http", host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
