# Two-stage build: node-based build then nginx-served static.
# The image does NOT bake a token; the SPA prompts the user to paste it.

FROM node:20-alpine AS build
WORKDIR /app

# Cache deps separately so source-only edits don't bust npm install.
COPY package.json package-lock.json* ./
RUN npm ci --no-audit --no-fund

COPY tsconfig.json tsconfig.app.json tsconfig.node.json vite.config.ts tailwind.config.ts postcss.config.js components.json ./
COPY index.html ./
COPY public ./public
COPY src ./src

RUN npm run build

# Runtime stage: nginx-unprivileged variant (listens on 8080, runs as
# uid 101, ships with the right Linux file capabilities). Plain
# nginx:1.27-alpine can't bind to port 80 under a non-root pod
# securityContext even with NET_BIND_SERVICE added — the cap must be
# set on the nginx binary file itself, which unprivileged variant
# does and the default image doesn't.
FROM nginxinc/nginx-unprivileged:1.31-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/nginx.conf
EXPOSE 8080
# No HEALTHCHECK: nginx:alpine doesn't ship wget/curl. The Caddy proxy is the
# integration test — if the SPA is broken, `curl http://localhost/` fails
# externally and that's the signal we care about.
CMD ["nginx", "-g", "daemon off;"]
