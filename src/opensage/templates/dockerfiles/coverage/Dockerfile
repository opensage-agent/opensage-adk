ARG BASE_IMAGE=n132/arvo:29728-vul
FROM ${BASE_IMAGE}

RUN apt-get update && apt-get install -y curl

RUN curl -LsSf https://astral.sh/uv/install.sh | sh

RUN mkdir -p /app

# Install Python 3.12 using uv
ENV PATH="/root/.local/bin:$PATH"

# Create a local venv and install deps into it.
RUN uv venv --python 3.12 /app/.venv
ENV PATH="/app/.venv/bin:/root/.local/bin:$PATH"

# Required by standalone bash tools uploader scripts.
RUN uv pip install --python /app/.venv/bin/python neo4j msgspec setuptools
