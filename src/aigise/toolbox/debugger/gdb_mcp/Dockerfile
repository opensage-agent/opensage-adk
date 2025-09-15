FROM n132/arvo:67968-vul

RUN apt-get update && apt-get install -y curl python3 wget xz-utils

RUN mkdir /app
WORKDIR /app

RUN curl -LsSf https://astral.sh/uv/install.sh | sh
RUN curl -qsL 'https://install.pwndbg.re' | sh -s -- -t pwndbg-gdb
ENV PATH="/root/.local/bin:$PATH"

# disable pwndbg colors
# https://github.com/pwndbg/pwndbg/blob/dev/docs/tutorials/env-vars.md?plain=1#L9
ENV PWNDBG_DISABLE_COLORS=1

COPY ./gdb_mcp_server.py /app/gdb_mcp/gdb_mcp_server.py
COPY ./helper.py /app/gdb_mcp/helper.py

# install dependencies
RUN uv venv --python 3.12
RUN uv pip install mcp
RUN uv pip install pygdbmi

CMD ["uv","run","-m","gdb_mcp.gdb_mcp_server"]
