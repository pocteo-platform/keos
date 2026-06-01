# Multi-arch image builds for Keos. Drives the `make build-images`
# target. Honours POCTEO_REGISTRY env (default ghcr.io/your-org).
#
#   docker buildx bake -f deploy/buildx-bake.hcl push
#   POCTEO_REGISTRY=registry.example.com docker buildx bake -f deploy/buildx-bake.hcl push
#
# All three images are built linux/amd64 + linux/arm64 — the latter so the
# stack runs on Jetson / Talos / RPi clusters without re-pulling.

variable "REGISTRY" {
  default = "ghcr.io/your-org"
}

variable "TAG" {
  default = "0.1.0"
}

variable "PLATFORMS" {
  default = "linux/amd64,linux/arm64"
}

group "default" {
  targets = ["api", "mcp-server", "frontend"]
}

target "api" {
  context    = ".."
  dockerfile = "../agent/Dockerfile.api"
  tags       = ["${REGISTRY}/pocteo-api:${TAG}"]
  platforms  = split(",", PLATFORMS)
}

target "mcp-server" {
  context    = ".."
  dockerfile = "../mcp_server/Dockerfile"
  tags       = ["${REGISTRY}/pocteo-mcp-server:${TAG}"]
  platforms  = split(",", PLATFORMS)
}

target "frontend" {
  context    = "../frontend"
  dockerfile = "../frontend/Dockerfile"
  tags       = ["${REGISTRY}/pocteo-frontend:${TAG}"]
  platforms  = split(",", PLATFORMS)
}
