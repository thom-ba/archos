# syntax=docker/dockerfile:1
#
# The image of the ci-shadow runner pods: no Docker daemon, no sudo needed at run
# time. Based on deploy/ci-runner/Dockerfile from archos-at/archos#1114, plus what
# CI's jobs would otherwise take from a container or install with sudo. Postgres
# stays on GitHub's major so results compare with the hosted runs.

FROM zricethezav/gitleaks:v8.30.1@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f AS gitleaks
FROM aquasec/trivy:0.61.1@sha256:91bcccab7cb503127b0d792db7c652c4e556c9ff30eeb0908b6ae1980d7727ad AS trivy

FROM ghcr.io/actions/actions-runner:2.338.0@sha256:4ffadc0002b2581327e06101fc8c06cd189232baf79fe561fac9caeb76f5e807

USER root
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Production's Postgres major (CloudNativePG `postgresql:17`), for the throwaway
# clusters scripts/rls-test-db.sh builds. Ubuntu 24.04 ships 16, so the PGDG
# repository it is. Only the server binaries: no cluster is created at build time.
ARG POSTGRES_MAJOR=16
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl gnupg xz-utils build-essential \
      git git-lfs jq zstd unzip zip file locales tzdata rsync openssh-client shellcheck \
      python3 python3-venv python3-pip \
 && install -d /usr/share/postgresql-common/pgdg \
 && curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc https://www.postgresql.org/media/keys/ACCC4CF8.asc \
 && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt noble-pgdg main" \
      > /etc/apt/sources.list.d/pgdg.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends "postgresql-${POSTGRES_MAJOR}" \
 && rm -rf /var/lib/apt/lists/*

# Node on the PATH before any setup step: setup-task and setup-bun install with
# `npm i -g`, relying on every GitHub runner image shipping Node.
ARG NODE_VERSION=22.23.3
ARG NODE_SHA256=df450af89261115ef9f9e3830c3eeb2cc9213b63c720b1af623cb5dcbe2e02de
RUN curl -fsSL -o /tmp/node.tar.xz "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz" \
 && echo "${NODE_SHA256}  /tmp/node.tar.xz" | sha256sum -c - \
 && tar -xJf /tmp/node.tar.xz -C /usr/local --strip-components=1 \
 && rm /tmp/node.tar.xz

# Chromium's system libraries for the website's accessibility gate, which CI
# otherwise installs with sudo. The version is apps/website's playwright.
RUN npx --yes playwright@1.63.0 install-deps chromium && rm -rf /root/.npm /var/lib/apt/lists/*

# Semgrep, which CI otherwise runs as a job container. Same version.
RUN python3 -m venv /opt/semgrep \
 && /opt/semgrep/bin/pip install --no-cache-dir semgrep==1.177.0 \
 && ln -s /opt/semgrep/bin/semgrep /usr/local/bin/semgrep

# The Qdrant ci/qdrant_local.sh starts.
ARG QDRANT_VERSION=1.19.2
ARG QDRANT_SHA256=34a788a09a4cb278b5c6d2af96d9a6db30c8c7e88ae3550455f7a4d425bb8d4b
RUN curl -fsSL -o /tmp/qdrant.tar.gz "https://github.com/qdrant/qdrant/releases/download/v${QDRANT_VERSION}/qdrant-x86_64-unknown-linux-gnu.tar.gz" \
 && echo "${QDRANT_SHA256}  /tmp/qdrant.tar.gz" | sha256sum -c - \
 && install -d "/opt/qdrant/${QDRANT_VERSION}" \
 && tar -xzf /tmp/qdrant.tar.gz -C "/opt/qdrant/${QDRANT_VERSION}" qdrant \
 && rm /tmp/qdrant.tar.gz

# The scanners CI otherwise runs with `docker run`, which a runner without a
# Docker daemon cannot: the same binaries, out of the same pinned images.
COPY --from=gitleaks /usr/bin/gitleaks /usr/local/bin/gitleaks
COPY --from=trivy /usr/local/bin/trivy /usr/local/bin/trivy

# A UTF-8 locale, as GitHub's runner image sets: without one, Python and initdb
# fall back to ASCII.
ENV LANG=C.UTF-8 LC_ALL=C.UTF-8

# `npm i -g` as the runner user, without sudo.
ENV NPM_CONFIG_PREFIX=/home/runner/.npm-global
ENV PATH=/home/runner/.npm-global/bin:/usr/lib/postgresql/${POSTGRES_MAJOR}/bin:${PATH}
RUN install -d -o runner -g runner /home/runner/.npm-global /opt/hostedtoolcache

# GitHub's tool cache path: setup-python's builds carry it as their RUNPATH.
ENV RUNNER_TOOL_CACHE=/opt/hostedtoolcache AGENT_TOOLSDIRECTORY=/opt/hostedtoolcache

USER runner
