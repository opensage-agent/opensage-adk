ARG BASE_IMAGE=ubuntu:22.04
FROM ${BASE_IMAGE}

# Switch to root for package installation
USER root

# Install dependencies - support both apt-get (Debian/Ubuntu) and apk (Alpine)
RUN set -e; \
    if command -v apt-get >/dev/null 2>&1; then \
        for f in /var/lib/dpkg/info/*.list; do \
            if [ -f "$f" ] && [ -s "$f" ]; then \
                tail -c1 "$f" | read -r _ || echo "" >> "$f"; \
            fi; \
        done && \
        apt-get update && \
        apt-get install -y curl && \
        (apt-get install -y ctags \
          || apt-get install -y universal-ctags \
          || apt-get install -y exuberant-ctags \
          || echo "WARNING: ctags not available; continuing without it."); \
    elif command -v apk >/dev/null 2>&1; then \
        apk add --no-cache curl bash && \
        (apk add --no-cache ctags \
          || apk add --no-cache universal-ctags \
          || echo "WARNING: ctags not available; continuing without it."); \
    else \
        echo "WARNING: Unknown package manager; skipping dependency installation."; \
    fi
RUN curl -LsSf https://astral.sh/uv/install.sh | sh

RUN mkdir -p /app

# Install Python 3.12 using uv
ENV PATH="/root/.local/bin:$PATH"

RUN uv venv --python 3.12 /app/.venv
ENV PATH="/app/.venv/bin:/root/.local/bin:$PATH"
RUN uv pip install --python /app/.venv/bin/python neo4j setuptools
ENTRYPOINT []
