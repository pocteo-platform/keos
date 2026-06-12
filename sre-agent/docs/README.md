---
id: intro
slug: /
title: Keos SRE Agent
sidebar_position: 0
description: The autonomous SRE + security agent that lives inside every Keos edge node. Start here.
---

# Keos SRE Agent

The autonomous SRE + security agent that runs **inside** every Keos edge node —
it observes the cluster, diagnoses incidents with a local LLM + retrieval,
remediates through a hard policy gate, verifies the fix, and signs every action.
No cloud dependency; air-gap ready.

```bash
keosctl install     # install onto any cluster (k3s / Talos / Kairos / Jetson / kubeadm)
keosctl ui          # open the web UI
keosctl run lib-reverse-shell   # watch the agent contain a live threat
```

## The docs

| Doc | Read it for |
|---|---|
| **[User & Operator Guide](./USER_GUIDE.md)** | running Keos day to day — modes, UI, use cases, audit |
| **[Architecture](./ARCHITECTURE.md)** | how it works — the loop, the safety mesh, the data planes |
| **[Installation](./INSTALLATION.md)** | install on any distro/arch — Linux, ARM+AMD, Jetson GPU, private registry, air-gap |
| **[CLI Reference](./CLI_REFERENCE.md)** | every `keosctl` command |
| **[Operations & Release](./OPERATIONS.md)** | build/publish, GitOps, GPU, observability, backup, release |
| **[Developer Guide](./DEVELOPER_GUIDE.md)** | repo layout, dev env, tests, extending the agent |
| **[Runbooks](./runbooks/README.md)** | incident-response playbooks for each failure mode |

New here? Start with the **User Guide**, then **Installation**.
