---
id: shell-completion
slug: /cli/shell-completion
title: Shell completion
sidebar_position: 6
description: bash, zsh, and fish completion scripts for keosctl — generated via `keosctl completion <shell>`.
---

# Shell completion

`keosctl` ships completion for the three major shells. Each is generated on demand by `keosctl completion <shell>`.

## bash

Source the script in your `.bashrc`:

```bash
# Persistent
echo 'eval "$(keosctl completion bash)"' >> ~/.bashrc
source ~/.bashrc

# One-shot (current shell only)
eval "$(keosctl completion bash)"
```

What gets completed:

- **First arg**: all 23 keosctl commands (`install`, `uninstall`, `status`, …)
- **`logs <TAB>` / `restart <TAB>` / `shell <TAB>` / `describe <TAB>` / `port-forward <TAB>`**: the 8 component names (`api`, `mcp-server`, `frontend`, `caddy`, `ollama`, `qdrant`, `nats`, `opa`)
- **`mode <TAB>`**: `observe-only`, `active`, `frozen`
- **`models <TAB>`**: `list`, `pull`, `rm`, `show`
- **`audit <TAB>`**: `tail`, `verify`
- **`usecases <TAB>`**: `list`, `show`
- **`completion <TAB>`**: `bash`, `zsh`, `fish`

## zsh

```zsh
# Persistent
echo 'eval "$(keosctl completion zsh)"' >> ~/.zshrc
source ~/.zshrc
```

Uses `_describe` for nice tab-cycle behaviour. Same coverage as bash for first-arg completion. Sub-arg completion is bash-only for now (zsh contribution welcome).

## fish

```fish
keosctl completion fish > ~/.config/fish/completions/keosctl.fish
```

Reloads automatically next time you open a fish shell. Completes the top-level command list; sub-arg completion isn't shipped yet.

## Troubleshooting

### "Tab completion gives me file names instead of commands"

The completion script isn't loaded. Check:

```bash
type -t _keosctl_complete     # should print: function
```

If empty, your `.bashrc` either doesn't source the keosctl completion or the file isn't being read. Common cause: `~/.bashrc` only runs in interactive shells — check that the line `eval "$(keosctl completion bash)"` is in `.bashrc` (not `.bash_profile` or `.profile`).

### "I added a new keosctl command but completion doesn't see it"

Completion is generated at the time you ran `eval`. Re-eval to pick up changes:

```bash
eval "$(keosctl completion bash)"
```

For permanent installs (sourced from `.bashrc`), open a fresh shell.

### "I want completion in a CI runner"

CI runners usually run non-interactive shells, so completion doesn't help. Use the explicit subcommand names directly. If you really want it (e.g. for an interactive `gh actions debug` session), add the same `eval` line to whatever profile your runner sources.

## See also

- [CLI overview](./) — the full command surface
- [Install + lifecycle](./install-uninstall) — what `install` actually does
