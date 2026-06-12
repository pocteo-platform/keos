# Jetson (Tegra iGPU) ollama variant.
#
# Stock ollama/ollama arm64 images compile CUDA kernels for server-class
# ARM only (sm_75..sm_121) and OMIT sm_87 (Jetson Orin iGPU) — the GPU is
# enumerated then skipped, silently falling back to CPU. ollama publishes
# the missing runner per release as ollama-linux-arm64-jetpack6.tar.zst
# (CUDA 12.6 kernels WITH sm_87); overlaying it restores GPU inference.
#
# Pair this image with env JETSON_JETPACK=6 on the container: ollama only
# auto-selects cuda_jetpack* runners when it recognises the L4T release in
# /etc/nv_tegra_release, and R39+ (JetPack 7) is unknown to 0.30.x.
# (OLLAMA_LLM_LIBRARY does NOT bypass that check, despite the skip log.)
# The installer's Tegra path sets it — see scripts/install-edge.sh.
#
# Validated on Jetson Orin Nano, L4T R39.2 / CUDA 13.2 / driver 595.78:
# 100% GPU, see docs/JETSON_VALIDATION_AND_PORTABILITY.md.

ARG OLLAMA_VERSION=0.30.7
FROM ollama/ollama:${OLLAMA_VERSION}

ARG OLLAMA_VERSION
ADD https://github.com/ollama/ollama/releases/download/v${OLLAMA_VERSION}/ollama-linux-arm64-jetpack6.tar.zst /tmp/jp6.tar.zst

RUN apt-get update \
 && apt-get install -y --no-install-recommends zstd \
 && tar --zstd -xf /tmp/jp6.tar.zst -C /usr/ \
 && test -d /usr/lib/ollama/cuda_jetpack6 \
 && rm /tmp/jp6.tar.zst \
 && apt-get purge -y zstd \
 && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/*
