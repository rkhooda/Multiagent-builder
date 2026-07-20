# syntax=docker/dockerfile:1

# ── Stage 1: node dependencies ───────────────────────────────────────────────
# @babel/parser only, installed from the lockfile. Day 22 chose Babel over
# esbuild precisely because it is pure JS with no per-arch native binary, which
# is what makes this stage deterministic and architecture-independent.
FROM node:20-slim AS jsdeps
WORKDIR /scripts
COPY scripts/package.json scripts/package-lock.json ./
RUN npm ci

# ── Stage 2: runtime ─────────────────────────────────────────────────────────
FROM python:3.11-slim

# Day 22's JS deep validation shells out to `node` from Python. Without it the
# validator silently degrades to brace-counting inside the container — the one
# environment this app actually ships in — and generated React could carry a
# real syntax error nobody notices. node:20-slim and python:3.11-slim are both
# bookworm, so the dynamic links resolve.
#
# `node --version` is not a smoke test for its own sake: it converts a broken
# binary copy into a BUILD failure, instead of a silent heuristic-mode
# downgrade discovered weeks later in generated code.
COPY --from=node:20-slim /usr/local/bin/node /usr/local/bin/node
RUN node --version

WORKDIR /app

# Dependency layer first, alone: editing an agent must not re-run pip install.
# The npm side is isolated in its own stage and cached independently, so the
# two installs never invalidate each other.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
# After COPY . . so node_modules wins regardless of build context contents.
# Re-copying 5MB from a cached stage is free; getting the order wrong is not.
COPY --from=jsdeps /scripts/node_modules ./scripts/node_modules

EXPOSE 8000

# No --reload: it is a dev-only file watcher that would also restart the server
# mid-pipeline whenever a generated file lands under outputs/.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
