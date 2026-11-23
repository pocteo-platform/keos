# Self-healing MCP server image.
#
# Thin: only what the server needs to call kubectl + OPA + emit audit lines.
# kubectl is included so the underlying agent.tools.* functions work; the
# container kubeconfig (./.kube/config-container) is mounted at runtime.

FROM python:3.15-slim

# kubectl + uv + tini (clean signal handling for the server).
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates tini \
 && rm -rf /var/lib/apt/lists/* \
 && KUBECTL_VERSION="v1.31.5" \
 && curl -fsSL -o /usr/local/bin/kubectl "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/$(dpkg --print-architecture)/kubectl" \
 && chmod +x /usr/local/bin/kubectl \
 && pip install --no-cache-dir --upgrade uv

WORKDIR /app

# Copy source. We don't use `uv sync --frozen` because the host's uv.lock
# may reflect a Windows-specific resolution; let uv resolve fresh inside
# the linux container.
COPY pyproject.toml /app/
COPY agent /app/agent
COPY mcp_server /app/mcp_server

# Install only the runtime deps (skip pytest/respx/etc).
RUN uv pip install --system --no-cache .

# No KUBECONFIG here: in-cluster, a baked path poisons kubectl (it
# hard-fails loading an unreadable /root/... as uid 1000 instead of
# falling back to the ServiceAccount — this broke isolate_pod on the
# Jetson). The compose path sets it per-service in docker-compose.yml.
# HOME=/tmp keeps kubectl's default config chain on a readable dir.
ENV PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    LOG_LEVEL=INFO

# The HTTP port. stdio transport doesn't open ports (it's spawned by the client).
EXPOSE 8090

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "mcp_server", "--transport", "http", "--host", "0.0.0.0", "--port", "8090"]
