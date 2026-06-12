# Multi-arch image builds for Keos. Driven by scripts/release.sh (or
# directly: `docker buildx bake -f deploy/buildx-bake.hcl --push`).
#
#   REGISTRY=registry.fleet.local:5000 TAG=1.1.0 \
#     docker buildx bake -f deploy/buildx-bake.hcl --push
#
# Image names match what the chart + install-edge.sh expect:
#   ${REGISTRY}/pocteo-platform/keos-api:${TAG}        (firstParty, via
#   ${REGISTRY}/pocteo-platform/keos-mcp-server:${TAG}  global.imageRegistry=
#   ${REGISTRY}/pocteo-platform/keos-frontend:${TAG}    ${REGISTRY}/pocteo-platform)
#   ${REGISTRY}/pocteo-platform/keos-ollama-jetson:${OLLAMA_VERSION}-jp6
#
# linux/arm64 needs QEMU on the build host (one-time):
#   docker run --privileged --rm tonistiigi/binfmt --install arm64
# Paths: `context` is resolved relative to THIS file's directory (deploy/),
# `dockerfile` relative to the context.

variable "REGISTRY" {
  default = "ghcr.io"
}

variable "TAG" {
  default = "1.1.0"
}

variable "PLATFORMS" {
  default = "linux/amd64,linux/arm64"
}

variable "OLLAMA_VERSION" {
  default = "0.30.7"
}

group "default" {
  targets = ["api", "mcp-server", "frontend"]
}

group "all" {
  targets = ["api", "mcp-server", "frontend", "ollama-jetson"]
}

target "api" {
  context    = ".."
  dockerfile = "agent/Dockerfile.api"
  tags       = ["${REGISTRY}/pocteo-platform/keos-api:${TAG}"]
  platforms  = split(",", PLATFORMS)
}

target "mcp-server" {
  context    = ".."
  dockerfile = "mcp_server/Dockerfile"
  tags       = ["${REGISTRY}/pocteo-platform/keos-mcp-server:${TAG}"]
  platforms  = split(",", PLATFORMS)
}

target "frontend" {
  context    = "../frontend"
  dockerfile = "Dockerfile"
  tags       = ["${REGISTRY}/pocteo-platform/keos-frontend:${TAG}"]
  platforms  = split(",", PLATFORMS)
}

# Jetson (Tegra iGPU) ollama variant — arm64 only. Official image + the
# official jetpack6 runner overlay (CUDA 12.6 kernels compiled for sm_87,
# which the stock arm64 image omits). The CUDA 12.x runner works on newer
# host drivers via minor-version compatibility. See
# docs/JETSON_VALIDATION_AND_PORTABILITY.md §1.4d.
target "ollama-jetson" {
  context    = "ollama-jetson"
  dockerfile = "Dockerfile"
  tags       = ["${REGISTRY}/pocteo-platform/keos-ollama-jetson:${OLLAMA_VERSION}-jp6"]
  platforms  = ["linux/arm64"]
  args = {
    OLLAMA_VERSION = "${OLLAMA_VERSION}"
  }
}
