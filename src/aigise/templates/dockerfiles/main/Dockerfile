ARG BASE_IMAGE=n132/arvo:29728-vul
FROM ${BASE_IMAGE}

RUN apt-get update && apt-get install -y curl ctags && \
    rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/install.sh | sh

RUN mkdir /app
WORKDIR /app

# Install Python 3.12 using uv
ENV PATH="/root/.local/bin:$PATH"

# Create a local venv (like gdb_mcp) and install deps into it.
RUN uv venv --python 3.12
ENV PATH="/app/.venv/bin:/root/.local/bin:$PATH"
RUN uv pip install neo4j
