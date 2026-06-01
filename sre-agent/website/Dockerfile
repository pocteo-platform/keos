# Multi-stage build for the Keos docs site.
#
#   Stage 1 (build)  — node:20-alpine. Installs all deps, builds the
#                       static site with `npm run build` → ./build/
#   Stage 2 (serve)  — nginx:1.27-alpine. Copies the static output and
#                       serves it on :80 (mapped to host :3000 by compose).
#
# The build is hermetic — no network calls at runtime, no Node, no live
# dev server. Just static HTML/JS/CSS.

# ───────────────────── Build stage ─────────────────────
FROM node:20-alpine AS build
WORKDIR /docs

# Install deps first (separate layer so docs/source edits don't bust the
# npm cache). The `npm ci` honours package-lock.json for reproducibility.
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

# Now bring in the rest and build.
COPY tsconfig.json docusaurus.config.ts sidebars.ts ./
COPY src ./src
COPY static ./static
COPY i18n ./i18n
COPY docs ./docs

# CI=true makes broken internal links fail the build instead of warn.
ENV CI=true
# DOCS_BASE_URL controls the URL prefix. Compose serves the docs at the
# host's root (port 3000 → /), so we override the GitHub-Pages default
# of /keos/.
ARG DOCS_BASE_URL=/
ENV DOCS_BASE_URL=${DOCS_BASE_URL}
# Force absolute URL to satisfy Docusaurus's URL validator at build time;
# only used for canonical link tags + sitemap.
ARG DOCS_URL=http://localhost:3000
ENV DOCS_URL=${DOCS_URL}

RUN npm run build

# ───────────────────── Serve stage ─────────────────────
FROM nginx:1.27-alpine AS serve

# Tighter security defaults than the stock nginx image.
RUN rm -rf /usr/share/nginx/html/* /etc/nginx/conf.d/default.conf

COPY --from=build /docs/build /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Run as non-root: the alpine image's `nginx` user already exists; we
# fix ownership on the cached/runtime dirs nginx writes to.
RUN chown -R nginx:nginx /usr/share/nginx/html /var/cache/nginx /var/log/nginx \
 && touch /var/run/nginx.pid \
 && chown nginx:nginx /var/run/nginx.pid

USER nginx
EXPOSE 80

# Sensible defaults; compose adds the healthcheck below.
CMD ["nginx", "-g", "daemon off;"]
