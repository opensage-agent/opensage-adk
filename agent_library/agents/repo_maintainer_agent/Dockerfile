ARG BASE_IMAGE=ubuntu:22.04
FROM ${BASE_IMAGE}

USER root

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Etc/UTC

RUN set -e; \
    if command -v apt-get >/dev/null 2>&1; then \
        apt-get update && \
        apt-get install -y --no-install-recommends \
            ca-certificates curl git git-lfs less ripgrep jq \
            build-essential python3 python3-venv python3-pip \
            locales tzdata && \
        locale-gen en_US.UTF-8 && \
        rm -rf /var/lib/apt/lists/*; \
    elif command -v apk >/dev/null 2>&1; then \
        apk add --no-cache bash ca-certificates curl git git-lfs less ripgrep jq build-base python3 py3-pip; \
    else \
        echo "WARNING: unknown package manager" && exit 1; \
    fi

RUN curl -LsSf https://astral.sh/uv/install.sh | sh

ENV LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8
ENV PATH="/root/.local/bin:/app/.venv/bin:$PATH"

RUN uv venv --python 3.12 /app/.venv && \
    uv pip install --python /app/.venv/bin/python neo4j setuptools

# Safety: make the maintained repo a "safe directory" regardless of host uid.
RUN git config --global --add safe.directory /workspace && \
    git config --global --add safe.directory '*'

ENTRYPOINT []
