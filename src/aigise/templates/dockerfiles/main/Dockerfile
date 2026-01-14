ARG BASE_IMAGE=n132/arvo:29728-vul
FROM ${BASE_IMAGE}

RUN set -e; \
    apt-get update; \
    apt-get install -y curl; \
    (apt-get install -y ctags \
      || apt-get install -y universal-ctags \
      || apt-get install -y exuberant-ctags \
      || echo "WARNING: ctags not available in this base image; continuing without it.");
RUN curl -LsSf https://astral.sh/uv/install.sh | sh

RUN mkdir -p /app

# Install Python 3.12 using uv
ENV PATH="/root/.local/bin:$PATH"

# Create a local venv (like gdb_mcp) and install deps into it.
RUN uv venv --python 3.12 /app/.venv
ENV PATH="/app/.venv/bin:/root/.local/bin:$PATH"
RUN uv pip install --python /app/.venv/bin/python neo4j
