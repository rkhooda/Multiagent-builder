# syntax=docker/dockerfile:1

# ── Stage 1: build the SPA ───────────────────────────────────────────────────
FROM node:20-alpine AS build
WORKDIR /app

# Manifest + lockfile alone, before source: editing a component must not
# re-run npm ci. `npm ci` (not `install`) so the build resolves exactly the
# locked tree and cannot silently drift to a newer minor.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

# ── Stage 2: serve ───────────────────────────────────────────────────────────
# Only the built assets cross this line — no node_modules, no source, no
# toolchain. The runtime image is nginx plus a directory of static files.
#
# No build args and no VITE_* env: every request in this app uses a relative
# URL, so the bundle carries no hostname and the same image works at localhost,
# on a LAN IP from a phone, or behind a domain.
FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
