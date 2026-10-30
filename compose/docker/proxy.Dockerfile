# syntax=docker/dockerfile:1.7
# sbam-proxy (TD §20.1, A-08b #554): the public reverse proxy, Caddy plus ONE third-party module,
# `caddy-ratelimit`, which stock Caddy lacks. It adds the per-IP `rate_limit` directive the Caddyfile
# uses on the portal host. Everything else is the stock Caddy image the deploy used before.
# Pinned by digest (builder and runtime) and by module commit: nothing here follows a tag.
FROM caddy:2.11.7-builder-alpine@sha256:ef9b65adf9a224770c6d5f6d1ae1bf99bc3caaa78d8c4bae3ea949ee6984f301 AS build
RUN xcaddy build v2.11.7 \
      --with github.com/mholt/caddy-ratelimit@5625512f24f6f59d6f64fb3aafe5eecff0b286db \
      --output /out/caddy

FROM caddy:2.11.7-alpine@sha256:d8542f48d34a9cf4e4c11a478865229840e87e4c96ea3f439101f31a5d35f75f
LABEL org.opencontainers.image.source="https://github.com/solutionsDigibull/BAM" \
      org.opencontainers.image.vendor="Digibull" \
      org.opencontainers.image.title="sbam-proxy" \
      org.opencontainers.image.description="SBAM reverse proxy: Caddy with per-IP rate limiting"
COPY --from=build /out/caddy /usr/bin/caddy
