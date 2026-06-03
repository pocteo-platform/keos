# Keos SRE Agent — documentation

This is the top-level documentation index. Start with **ARCHITECTURE** to
understand the system, then **USER_GUIDE** for day-to-day operations.

| File | What's in it |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | **The bible.** Macro + per-part Mermaid diagrams, the LangGraph loop (10 nodes), the safety mesh, the data planes, deployment shapes, one end-to-end request lifecycle, and the §8 *Continuous intelligence* layer covering Phases 12-15 (calibration, ML baselines, predictive forecasting, GitOps drift detection). |
| [USER_GUIDE.md](USER_GUIDE.md) | Architecture overview, install paths, config reference, day-2 ops, troubleshooting, security model, audit verification, FAQ. |
| [INSTALL.md](INSTALL.md) | Installation matrix: Docker Desktop (laptop demo), generic K8s, k3s on Jetson, **Talos**, air-gapped. |
| [TALOS.md](TALOS.md) | Talos-specific notes: containerd socket path, machine config, air-gapped image cache, troubleshooting. |
| [JETSON.md](JETSON.md) | Jetson Orin Nano + k3s + GPU. Full walkthrough from JetPack flash to a ~25 s graph run. |
| [CONFIG_REFERENCE.md](CONFIG_REFERENCE.md) | Every env var + values.yaml key documented. |
| [UI_GUIDE.md](UI_GUIDE.md) | Web UI walkthrough with screenshots — the 5 views + the wizard. |
| [USE_CASES.md](USE_CASES.md) | **Hands-on tutorial.** Five end-to-end scenarios with Chaos Mesh, step-by-step wizard inputs, and what to look at in the UI for each. |
| [CLAUDE_DESKTOP.md](CLAUDE_DESKTOP.md) | Wiring Keos's MCP server into Claude Desktop. |
| [OBSERVABILITY.md](OBSERVABILITY.md) | Prometheus metrics, alert rules, LogQL recipes. |
| [BACKUP_RESTORE.md](BACKUP_RESTORE.md) | Velero + manual backup recipes; tested restore drill. |
| [RUNBOOKS/](RUNBOOKS/README.md) | **Incident response.** 10 runbooks + decision-tree index. |
| [RELEASE_NOTES_1.0.0-rc.1.md](RELEASE_NOTES_1.0.0-rc.1.md) | First release candidate — highlights, install, known limitations. |
| [../CHANGELOG.md](../CHANGELOG.md) | Every release, every phase, backfilled from Phase 1. |
| [../POSITIONING.md](../POSITIONING.md) | Where Keos sits vs kagent, K8sGPT, Botkube, Cast AI. |
| [../DEMO.md](../DEMO.md) | Spoken-walkthrough scripts for live demos (A, B, C). |
| [../README.md](../README.md) | One-paragraph elevator pitch + `make demo-ui` quick start. |
