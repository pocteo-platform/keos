# Keos documentation site

[Docusaurus 3](https://docusaurus.io) site that surfaces every markdown
file under `sre-agent/docs/` with full-text search, EN/FR navigation, and
the architecture diagrams rendered as live Mermaid.

The markdown lives in `sre-agent/docs/`. **Do not duplicate it here.**
This directory only contains:

- `docusaurus.config.ts` — site configuration (URL, navbar, footer, themes)
- `sidebars.ts` — manual sidebar ordering across User Guide / Operations / Reference
- `src/css/custom.css` — Keos brand tokens (matches `frontend/src/styles/globals.css`)
- `src/pages/index.tsx` — landing page (hero + 4 feature cards)
- `static/img/*` — logo + favicon placeholders
- `i18n/fr/` — French translation scaffolding (currently empty; falls back to EN)

## Develop

```bash
cd sre-agent/website
npm install            # ~80 MB into node_modules — gitignored
npm start              # serves on http://localhost:3000 with hot reload
```

The dev server picks up changes to the markdown under `sre-agent/docs/`
without restart. Sidebar reorders + `docusaurus.config.ts` changes do
need a restart.

## Build

```bash
npm run build          # outputs static site to ./build/
npm run serve          # serves ./build/ on http://localhost:3000 to verify
```

Production builds run with `onBrokenLinks: throw` (set via the `CI`
env var) so any dangling cross-doc link fails CI loudly.

## Type-check

```bash
npm run typecheck      # tsc -p .  — verifies docusaurus.config.ts + src/
```

## Deploy

CI is wired in `.github/workflows/docs.yml` (at the repo root). It
builds on every push to `main` and deploys to GitHub Pages from the
`gh-pages` branch — the URL ends up at
`https://pocteo-platform.github.io/keos`.

For one-off manual deploys:

```bash
GIT_USER=<your-github-handle> npm run deploy
```

Override `DOCS_URL` / `DOCS_BASE_URL` env vars to host elsewhere (e.g.,
self-hosted under `/internal/keos/`).

## i18n

Strings are EN by default. To translate to FR:

```bash
npm run write-translations -- --locale fr
```

This generates JSON files under `i18n/fr/code.json` for theme strings
(navbar, footer, search). Doc translations go into
`i18n/fr/docusaurus-plugin-content-docs/current/` mirroring the
`sre-agent/docs/` tree — missing files fall back to EN.

## Adding a new doc

1. Create the markdown under `sre-agent/docs/<NAME>.md` (no front-matter
   required — the H1 becomes the page title).
2. Add the doc ID `<NAME>` to one of the sidebars in `sidebars.ts`.
3. Reload `npm start` and verify it shows up.

Cross-links between docs use plain markdown:

```markdown
See the [architecture overview](ARCHITECTURE.md) for…
```

Docusaurus rewrites these to its routing system at build time.
