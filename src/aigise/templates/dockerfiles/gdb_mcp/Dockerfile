ARG BASE_IMAGE=ubuntu:20.04
FROM ${BASE_IMAGE}

RUN apt-get update && apt-get install -y curl python3 wget xz-utils

RUN mkdir /app
WORKDIR /app

RUN curl -LsSf https://astral.sh/uv/install.sh | sh
# RUN curl -qsL 'https://install.pwndbg.re' | sh -s -- -t pwndbg-gdb
RUN wget https://github.com/pwndbg/pwndbg/releases/download/2024.08.29/pwndbg_2024.08.29_amd64.deb && dpkg -i pwndbg_2024.08.29_amd64.deb

ENV PATH="/root/.local/bin:$PATH"

# disable pwndbg colors
# https://github.com/pwndbg/pwndbg/blob/dev/docs/tutorials/env-vars.md?plain=1#L9
ENV PWNDBG_DISABLE_COLORS=1

COPY ./artifacts/gdb_mcp_server.py /app/gdb_mcp/gdb_mcp_server.py
COPY ./artifacts/helper.py /app/gdb_mcp/helper.py

# install dependencies
RUN uv venv --python 3.12
RUN uv pip install mcp pygdbmi loguru

# overwrite IoManager.py to fix stderr reading issue to show asan messages
COPY ./artifacts/IoManager.py /app/.venv/lib/python3.12/site-packages/pygdbmi/IoManager.py

# Expose port for MCP server
EXPOSE 1111

CMD ["uv","run","-m","gdb_mcp.gdb_mcp_server"]
