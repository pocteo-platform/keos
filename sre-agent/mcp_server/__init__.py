"""MCP server exposing the self-healing agent's 5 action tools.

Tools are gated by the SAME OPA policy and read the SAME AgentMode CRD as
the LangGraph agent. The audit log is shared (records get a
``source: "mcp"`` field so agent-vs-operator actions are distinguishable).
"""

__version__ = "1.1.0"
