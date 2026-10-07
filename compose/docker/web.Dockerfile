# syntax=docker/dockerfile:1.7
# sbam-web image (TD §20.2): Next.js standalone output, non-root.
#   docker build -f compose/docker/web.Dockerfile --target prod -t sbam-web .
# Targets: `prod` (standalone server) and `dev` (client/ bind-mounted by compose.override.yaml, `next dev`).

FROM node:22.23-alpine@sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402 AS base
LABEL org.opencontainers.image.source="https://github.com/solutionsDigibull/BAM" \
      org.opencontainers.image.vendor="Digibull" \
      org.opencontainers.image.title="sbam-web"
ENV NEXT_TELEMETRY_DISABLED=1 \
    PNPM_HOME=/pnpm \
    PATH=/pnpm:$PATH
RUN corepack enable \
 && addgroup -g 10001 sbam \
 && adduser -u 10001 -G sbam -D -H -s /sbin/nologin sbam
WORKDIR /app/client

FROM base AS deps
COPY client/package.json client/pnpm-lock.yaml client/.npmrc ./
RUN --mount=type=cache,id=pnpm,target=/pnpm/store \
    pnpm install --frozen-lockfile

# --- dev: node_modules live in the image (Compose keeps them in a named volume) -------------------
FROM deps AS dev
COPY client/ ./
RUN chown -R sbam:sbam /app/client
USER 10001:10001
EXPOSE 3000
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=5 \
  CMD ["wget", "-q", "-O", "/dev/null", "http://127.0.0.1:3000/api/health"]
CMD ["pnpm", "exec", "next", "dev", "--hostname", "0.0.0.0", "--port", "3000"]

FROM deps AS build
COPY client/ ./
RUN pnpm build

# --- prod: only the standalone server + static assets --------------------------------------------
FROM node:22.23-alpine@sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402 AS prod
LABEL org.opencontainers.image.source="https://github.com/solutionsDigibull/BAM" \
      org.opencontainers.image.vendor="Digibull" \
      org.opencontainers.image.title="sbam-web"
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=3000
RUN addgroup -g 10001 sbam && adduser -u 10001 -G sbam -D -H -s /sbin/nologin sbam
WORKDIR /app
COPY --from=build --chown=10001:10001 /app/client/.next/standalone ./
COPY --from=build --chown=10001:10001 /app/client/.next/static ./.next/static
USER 10001:10001
EXPOSE 3000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
  CMD ["wget", "-q", "-O", "/dev/null", "http://127.0.0.1:3000/api/health"]
CMD ["node", "server.js"]
