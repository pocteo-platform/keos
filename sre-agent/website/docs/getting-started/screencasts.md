---
id: screencasts
slug: /getting-started/screencasts
title: Screencasts
sidebar_position: 5
description: A 5-episode terminal screencast series — keosctl driving a live Keos SRE Agent install, recorded with asciinema.
---

# Screencasts

A 5-episode series, recorded against a **live cluster** with [asciinema](https://asciinema.org). Every command is real — no mock output. Each episode is under 90 seconds.

> Want to follow along? `git clone https://github.com/pocteo-platform/keos && cd keos && bash sre-agent/scripts/keosctl install`

## Episode 0 — The install wizard

From a clean cluster to a running agent: the full 6-step wizard, the local-build fallback, the Helm install, the model pull, and the final `✓ Install complete` card. (Long pauses are compressed — this is the real install, recorded live.)

![Episode 0 — Install wizard](/casts/ep00-install.gif)

## Episode 1 — Status & health

The 60-second cluster check-in: `status` → `doctor` → `config view`.

![Episode 1 — Status & health](/casts/ep01-status.gif)

## Episode 2 — Run a use case

Trigger a synthetic memory leak and watch the agent pick it up: `usecases list` → `run lib-memory-leak` → `events`.

![Episode 2 — Run a use case](/casts/ep02-usecase.gif)

## Episode 3 — Introspection

What can the agent do, and what does it know? `policy test` (allow + hard-deny), `runbooks list`, `images list`.

![Episode 3 — Introspection](/casts/ep03-introspection.gif)

## Episode 4 — Day-2 operations

Production housekeeping: `backup` → `diag` (support bundle) → `models list` → `token`.

![Episode 4 — Day-2 operations](/casts/ep04-day2.gif)

## Episode 5 — Help tour

The full command menu + per-command help: `help` → `mode --help` → `backup -h`.

![Episode 5 — Help tour](/casts/ep05-help.gif)

## Raw `.cast` files

The original asciinema recordings are alongside the GIFs — play them in your terminal with full copy-paste-able text:

```bash
asciinema play https://pocteo-platform.github.io/keos/casts/ep01-status.cast
```

| Episode | GIF | Cast |
|---|---|---|
| 0 — Install wizard | [ep00-install.gif](/casts/ep00-install.gif) | [ep00-install.cast](/casts/ep00-install.cast) |
| 1 — Status & health | [ep01-status.gif](/casts/ep01-status.gif) | [ep01-status.cast](/casts/ep01-status.cast) |
| 2 — Run a use case | [ep02-usecase.gif](/casts/ep02-usecase.gif) | [ep02-usecase.cast](/casts/ep02-usecase.cast) |
| 3 — Introspection | [ep03-introspection.gif](/casts/ep03-introspection.gif) | [ep03-introspection.cast](/casts/ep03-introspection.cast) |
| 4 — Day-2 operations | [ep04-day2.gif](/casts/ep04-day2.gif) | [ep04-day2.cast](/casts/ep04-day2.cast) |
| 5 — Help tour | [ep05-help.gif](/casts/ep05-help.gif) | [ep05-help.cast](/casts/ep05-help.cast) |

## Re-recording the series

The episodes are scripted + reproducible. After any keosctl change:

```bash
# Requires: asciinema + agg + a live install in the current kubectl context
bash sre-agent/scripts/demo/record-all.sh sre-agent/website/static/casts
```

Each episode script lives in `sre-agent/scripts/demo/` — `cast-lib.sh` provides the typing simulator (25–70 ms per-keystroke jitter), the episode files are plain lists of `say_comment` + `play_cmd` calls. Adding episode 6 is a 10-line file.
