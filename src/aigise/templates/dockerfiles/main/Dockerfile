ARG BASE_IMAGE=n132/arvo:29728-vul
FROM ${BASE_IMAGE}

# Install dependencies - support both apt-get (Debian/Ubuntu) and apk (Alpine)
# Fix dpkg database corruption in some SWE-bench base images (missing newline in .list files)
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

# Create a local venv (like gdb_mcp) and install deps into it.
RUN uv venv --python 3.12 /app/.venv
ENV PATH="/app/.venv/bin:/root/.local/bin:$PATH"
RUN uv pip install --python /app/.venv/bin/python neo4j setuptools
ENTRYPOINT []
