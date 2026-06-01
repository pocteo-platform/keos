# Self-healing PoC top-level commands.
# Primary on Linux/macOS. Windows users invoke `.\make.ps1 <target>` instead
# (PS wrapper mirrors these targets verbatim).
#
# Conventions:
#   - Each recipe is a SINGLE command (or `;`-joined) so it works under both
#     bash and Windows `make` (chocolatey). No bash heredocs, no `&&` chains
#     spanning lines. Logic goes in Python helpers under `agent/`.
#   - `KUBECONFIG` is always read from .env (./.kube/config by default).

SHELL := /bin/bash
COMPOSE := docker compose
CLUSTER := poc
KUBECONFIG_FILE := ./.kube/config
OLLAMA_CHAT_MODEL := qwen2.5:3b-instruct-q4_K_M
OLLAMA_EMBED_MODEL := nomic-embed-text

# Many Linux distros (Ubuntu, Debian, RHEL 9+) ship only `python3` — no
# unsuffixed `python` exists. macOS Homebrew + WSL behave the same. Prefer
# python3, fall back to python only if python3 is missing.
PYTHON ?= $(shell command -v python3 2>/dev/null || command -v python 2>/dev/null || echo python3)

.PHONY: help install doctor up down restart rebuild rebuild-nocache rebuild-full logs ps seed pull-models cluster cluster-down \
        kubeconfig test test-policy demo demo-ui clean \
        lint lint-python lint-frontend format typecheck typecheck-python typecheck-frontend coverage

help:
	@echo "Targets:"
	@echo "  install       Install missing prereqs (k3d, opa, uv), pull Ollama models"
	@echo "  up            docker compose up -d (off-cluster services)"
	@echo "  down          docker compose down + delete k3d cluster"
	@echo "  pull-models   Pull Qwen2.5-3B + nomic-embed-text into Ollama"
	@echo "  seed          Load runbooks into Qdrant"
	@echo "  cluster       Create k3d cluster + apply manifests"
	@echo "  cluster-down  Delete the k3d cluster only"
	@echo "  test-policy   Run pytest against the OPA policy via HTTP"
	@echo "  test          Run all pytest suites"
	@echo "  demo          End-to-end CLI narrative: observe-only -> escalate, then active -> heal"
	@echo "  demo-ui       Bring everything up, open the SPA, auto-dispatch scenario 1"
	@echo "  logs          Tail logs from all compose services"
	@echo "  ps            Show compose + cluster status"
	@echo "  clean         down + drop named volumes (re-download models next time)"

install:
	@# Install uv if missing. Prefer the official standalone installer (no pip,
	@# no venv pitfalls); fall back to `python3 -m pip install --user uv`.
	@# Skip entirely if uv is already on PATH or in ~/.local/bin.
	@bash -c 'if command -v uv >/dev/null 2>&1 || [ -x "$$HOME/.local/bin/uv" ]; then \
		echo "uv already present; skipping install."; \
	else \
		echo "Installing uv via official installer..."; \
		if command -v curl >/dev/null 2>&1; then \
			curl -LsSf https://astral.sh/uv/install.sh | sh || \
				(echo "Official installer failed; falling back to pip..." && \
				 python3 -m pip install --user --upgrade uv); \
		elif command -v wget >/dev/null 2>&1; then \
			wget -qO- https://astral.sh/uv/install.sh | sh || \
				python3 -m pip install --user --upgrade uv; \
		else \
			python3 -m pip install --user --upgrade uv; \
		fi; \
	fi'
	@# Make uv resolvable for the rest of this recipe even if its install dir
	@# isn't on the current shell's PATH yet.
	@export PATH="$$HOME/.local/bin:$$PATH" && uv --version
	@# Install k3d if missing. Drops the binary in $$HOME/.local/bin (no sudo)
	@# so the rest of `install` picks it up via the PATH export below.
	@bash -c 'if command -v k3d >/dev/null 2>&1 || [ -x "$$HOME/.local/bin/k3d" ]; then \
		echo "k3d already present; skipping install."; \
	else \
		echo "Installing k3d v5.8.3 to $$HOME/.local/bin..."; \
		mkdir -p $$HOME/.local/bin; \
		OS=$$(uname -s | tr "[:upper:]" "[:lower:]"); ARCH=$$(uname -m); \
		case "$$ARCH" in x86_64|amd64) ARCH=amd64 ;; aarch64|arm64) ARCH=arm64 ;; esac; \
		URL="https://github.com/k3d-io/k3d/releases/download/v5.8.3/k3d-$${OS}-$${ARCH}"; \
		curl -fLsS -o $$HOME/.local/bin/k3d "$$URL" && chmod +x $$HOME/.local/bin/k3d; \
	fi'
	@bash -c 'OS=$$(uname -s); ARCH=$$(uname -m); case "$$OS" in Linux) URL="https://openpolicyagent.org/downloads/v0.69.0/opa_linux_amd64_static" ; OUT="./bin/opa" ;; Darwin) URL="https://openpolicyagent.org/downloads/v0.69.0/opa_darwin_amd64" ; OUT="./bin/opa" ;; *) URL="https://openpolicyagent.org/downloads/v0.69.0/opa_windows_amd64.exe" ; OUT="./bin/opa.exe" ;; esac; [ -f $$OUT ] || (mkdir -p bin && curl -fsSL -o $$OUT $$URL && chmod +x $$OUT || true)'
	@# Install helm if missing. Needed by k3d/bootstrap.sh to deploy Chaos Mesh
	@# (without it, chaos-driven use cases won't have an experiment controller).
	@bash -c 'if command -v helm >/dev/null 2>&1 || [ -x "$$HOME/.local/bin/helm" ]; then \
		echo "helm already present; skipping install."; \
	else \
		echo "Installing helm v3.16.4 to $$HOME/.local/bin..."; \
		mkdir -p $$HOME/.local/bin; \
		OS=$$(uname -s | tr "[:upper:]" "[:lower:]"); ARCH=$$(uname -m); \
		case "$$ARCH" in x86_64|amd64) ARCH=amd64 ;; aarch64|arm64) ARCH=arm64 ;; esac; \
		TMP=$$(mktemp -d); \
		curl -fLsS "https://get.helm.sh/helm-v3.16.4-$${OS}-$${ARCH}.tar.gz" | tar -xz -C $$TMP && \
		mv $$TMP/$${OS}-$${ARCH}/helm $$HOME/.local/bin/helm && \
		chmod +x $$HOME/.local/bin/helm && rm -rf $$TMP; \
	fi'
	@export PATH="$$HOME/.local/bin:$$PATH" && uv sync
	@# Re-export PATH so any sub-make invocations also see the just-installed
	@# user-local binaries (k3d, uv).
	@PATH="$$HOME/.local/bin:$$PATH" $(MAKE) up
	@PATH="$$HOME/.local/bin:$$PATH" $(MAKE) pull-models
	@PATH="$$HOME/.local/bin:$$PATH" $(MAKE) cluster
	@sleep 2
	@$(MAKE) seed
	@$(MAKE) doctor

# Diagnose every dependency. Prints what's broken AND the exact fix.
# Safe to run any time. Exits non-zero if anything needs attention.
doctor:
	@export PATH="$$HOME/.local/bin:$$PATH" && uv run python -m scripts.doctor

# -----------------------------------------------------------------------
# Code quality targets — wired into CI under .github/workflows/ci.yml.
# Each one composes a backend + frontend pass so `make lint` covers
# both at once. The split-out `lint-python` / `lint-frontend` targets
# exist for the CI matrix.
# -----------------------------------------------------------------------

lint: lint-python lint-frontend

lint-python:
	@export PATH="$$HOME/.local/bin:$$PATH" && uv run ruff check .

lint-frontend:
	@cd frontend && npm run lint

# `format` applies fixes in-place. `make lint` afterwards confirms clean.
format:
	@export PATH="$$HOME/.local/bin:$$PATH" && uv run ruff format . && uv run ruff check --fix .
	@cd frontend && (npm run lint -- --fix || true)

typecheck: typecheck-python typecheck-frontend

typecheck-python:
	@export PATH="$$HOME/.local/bin:$$PATH" && uv run mypy agent mcp_server scripts

typecheck-frontend:
	@cd frontend && npm run typecheck

# Coverage report (terminal + xml for CI uploads). Doesn't gate on a
# minimum % yet — Phase 10.7 ratchets that up after the cleanup pass.
coverage:
	@export PATH="$$HOME/.local/bin:$$PATH" && uv run pytest --cov --cov-report=term-missing --cov-report=xml

up: .env.local
	@# Seed empty kubeconfig placeholder files so Docker bind-mounts them as
	@# files, not directories. Without this, when `up` runs before `cluster`
	@# (no kubeconfig yet), Docker creates an empty DIRECTORY at the mount
	@# destination inside api / mcp-server / k8sgpt containers — and they
	@# can never read kubeconfig until recreated. See make.ps1 for the
	@# full explanation.
	@mkdir -p .kube
	@[ -e .kube/config ] || touch .kube/config
	@[ -e .kube/config-container ] || touch .kube/config-container
	@$(COMPOSE) up -d
	@echo "Compose stack starting. Use 'make ps' to see health."

# Generate a stable bearer token on first `up` so the SPA can read it.
# Atomic write — if $(PYTHON) errors mid-recipe, .env.local is NOT overwritten
# (cf. the trap where `> .env.local` truncates before the command runs and
# leaves an empty file behind that make then treats as "already built").
.env.local:
	@TMP=$$(mktemp) && \
	  $(PYTHON) -c "import secrets,sys; sys.stdout.write('SHP_API_TOKEN='+secrets.token_urlsafe(24)+'\n')" > $$TMP && \
	  mv $$TMP .env.local && \
	  echo "Generated SHP_API_TOKEN in .env.local"

down:
	@-$(COMPOSE) down
	@-k3d cluster delete $(CLUSTER) 2>/dev/null
	@echo "Stack down. Named volumes retained (use 'make clean' to drop)."

restart: down up

# Rebuild only the locally-built services (frontend, api, mcp-server)
# AND force-recreate the containers from the new images. `docker compose
# up --build` alone sometimes keeps the old running container if it can't
# detect a meaningful config change. --force-recreate guarantees fresh
# containers. Stateful services (ollama, qdrant, nats, opa, caddy) stay
# running.
rebuild:
	@$(COMPOSE) build api frontend mcp-server
	@$(COMPOSE) up -d --force-recreate --no-deps api frontend mcp-server
	@echo "Rebuilt. Hard-refresh the browser (Ctrl+Shift+R) to pick up new SPA assets."

# Same but invalidates Docker's layer cache first — use when you suspect
# cache poisoning (e.g. a deps file changed but Docker reused an old layer).
rebuild-nocache:
	@$(COMPOSE) build --no-cache api frontend mcp-server
	@$(COMPOSE) up -d --force-recreate --no-deps api frontend mcp-server
	@echo "Rebuilt (no cache). Hard-refresh the browser."

# Bullet-proof option: full down, build, up. Slowest (stateful services
# also restart, ~30s extra) but never misbehaves. Use if `rebuild` ever
# fails to surface your changes.
rebuild-full:
	@$(COMPOSE) down
	@$(COMPOSE) build api frontend mcp-server
	@$(COMPOSE) up -d
	@echo "Full rebuild complete. Hard-refresh the browser."

logs:
	@$(COMPOSE) logs -f --tail=100

ps:
	@$(COMPOSE) ps
	@echo "---"
	@k3d cluster list 2>/dev/null || true

pull-models:
	@$(PYTHON) agent/pull_models.py

seed:
	@export PATH="$$HOME/.local/bin:$$PATH" && uv run python -m agent.seed_rag

cluster:
	@k3d cluster list | grep -q "^$(CLUSTER) " || k3d cluster create --config k3d/cluster.yaml
	@# Generate the kubeconfig BEFORE bootstrap so kubectl applies have a server
	@# to talk to. cluster.yaml sets updateDefaultKubeconfig=false, so we own
	@# the file. (make.ps1 does the equivalent via Invoke-Kubeconfig.)
	@$(MAKE) kubeconfig
	@# If any compose containers bind-mounted the empty placeholder kubeconfig
	@# (api, mcp-server, k8sgpt — seeded in `up`), recreate them so they pick
	@# up the freshly-written file.
	@-running=$$($(COMPOSE) ps --services --status running 2>/dev/null); \
	  to_recreate=""; \
	  for svc in api mcp-server k8sgpt; do \
	    echo "$$running" | grep -qx "$$svc" && to_recreate="$$to_recreate $$svc" || true; \
	  done; \
	  if [ -n "$$to_recreate" ]; then \
	    echo "Recreating containers to pick up new kubeconfig:$$to_recreate"; \
	    $(COMPOSE) up -d --force-recreate --no-deps $$to_recreate; \
	  fi
	@bash k3d/bootstrap.sh

cluster-down:
	@k3d cluster delete $(CLUSTER)

kubeconfig:
	@mkdir -p .kube
	@# k3d on Docker Desktop emits 'server: https://host.docker.internal:<random>',
	@# which is unreachable from the host. Write two flavours:
	@#   .kube/config            -> 127.0.0.1:6550   (for host-side kubectl + agent)
	@#   .kube/config-container  -> host.docker.internal:6550 (for compose services
	@#                              like k8sgpt that need to reach back into the host)
	@k3d kubeconfig get $(CLUSTER) | sed -E 's#server: https://[^[:space:]]+#server: https://127.0.0.1:6550#' > $(KUBECONFIG_FILE)
	@k3d kubeconfig get $(CLUSTER) | sed -E 's#server: https://[^[:space:]]+#server: https://host.docker.internal:6550#' > .kube/config-container
	@# NOTE: do NOT chmod 600 here — config-container is bind-mounted into
	@# k8sgpt / mcp-server / api containers that run as non-root users with
	@# different UIDs than the host. helm + kubectl will warn about loose
	@# perms; that's cosmetic for the laptop dev cert and accepted upstream
	@# (the cert is regenerated on every `make cluster`).
	@echo "Wrote $(KUBECONFIG_FILE) and .kube/config-container"

test-policy:
	@export PATH="$$HOME/.local/bin:$$PATH" && uv run pytest -v tests/test_policy.py

test:
	@export PATH="$$HOME/.local/bin:$$PATH" && uv run pytest -v tests

demo:
	@export PATH="$$HOME/.local/bin:$$PATH" && uv run python -m agent.demo

demo-ui: up
	@export PATH="$$HOME/.local/bin:$$PATH" && uv run python -m agent.demo_ui

# Multi-arch builds for cluster install. Requires `docker buildx` set up
# with a builder that supports the platforms in deploy/buildx-bake.hcl.
build-images:
	@docker buildx bake -f deploy/buildx-bake.hcl push

# Install Keos into a target cluster via Helm. KUBECONFIG must point at
# the target. apiToken + hmacKey are required; minted on the fly if not
# overridden. NAMESPACE defaults to "pocteo".
NAMESPACE ?= pocteo
install-cluster:
	@helm dependency update deploy/helm/pocteo >/dev/null
	@helm upgrade --install pocteo deploy/helm/pocteo \
		--namespace $(NAMESPACE) --create-namespace \
		--set-string auth.apiToken=$$(openssl rand -base64 32) \
		--set-string auth.hmacKey=$$(openssl rand -base64 32) \
		--wait --timeout 5m

uninstall-cluster:
	@-helm uninstall pocteo -n $(NAMESPACE)
	@-kubectl delete ns $(NAMESPACE) --wait=false

clean: down
	@-docker volume rm shp_ollama_models shp_qdrant_data shp_nats_data 2>/dev/null
	@-rm -rf .kube bin agent/audit.log
	@echo "Clean. Next 'make install' will re-download models."
