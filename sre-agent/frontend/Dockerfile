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

# Runtime stage: tiny nginx image serving the built SPA.
FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/nginx.conf
EXPOSE 80
# No HEALTHCHECK: nginx:alpine doesn't ship wget/curl. The Caddy proxy is the
# integration test — if the SPA is broken, `curl http://localhost/` fails
# externally and that's the signal we care about.
CMD ["nginx", "-g", "daemon off;"]
