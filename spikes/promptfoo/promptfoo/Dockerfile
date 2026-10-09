# Spike E0.12 (#30): sbam-promptfoo image (TD §20: node:22.23-slim + pinned Promptfoo CLI).
# Network is only needed here, at build time (npm install). At run time the container has none.
FROM node:22.23-slim

WORKDIR /spike/promptfoo
COPY package.json ./
RUN npm install --omit=dev --no-audit --no-fund && npm cache clean --force
COPY run_suite.mjs ./

# No telemetry, no update check, no sharing; Promptfoo's own state goes to /tmp, not the image.
ENV PROMPTFOO_DISABLE_TELEMETRY=1 PROMPTFOO_DISABLE_UPDATE=1 PROMPTFOO_DISABLE_SHARING=1 \
    PROMPTFOO_CONFIG_DIR=/tmp/promptfoo
USER node
WORKDIR /spike
ENTRYPOINT ["node", "/spike/promptfoo/run_suite.mjs"]
CMD ["/spike/suite.yaml"]
