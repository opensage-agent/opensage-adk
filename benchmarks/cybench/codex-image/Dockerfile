FROM ghcr.io/opensage-agent/ctf_task_main:latest

RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm \
    && npm install -g @openai/codex mcp-remote \
    && rm -rf /var/lib/apt/lists/*

RUN wget https://github.com/pwndbg/pwndbg/releases/download/2024.08.29/pwndbg_2024.08.29_amd64.deb \
    && dpkg -i pwndbg_2024.08.29_amd64.deb \
    && rm pwndbg_2024.08.29_amd64.deb
ENV PWNDBG_DISABLE_COLORS=1

RUN uv pip install mcp pygdbmi loguru

COPY --from=ghcr.io/opensage-agent/gdb_mcp:latest /app/gdb_mcp/gdb_mcp_server.py /app/gdb_mcp/gdb_mcp_server.py
COPY --from=ghcr.io/opensage-agent/gdb_mcp:latest /app/gdb_mcp/helper.py /app/gdb_mcp/helper.py
COPY --from=ghcr.io/opensage-agent/gdb_mcp:latest /app/.venv/lib/python3.12/site-packages/pygdbmi/IoManager.py /tmp/IoManager.py
RUN python -c "import pathlib, pygdbmi; (pathlib.Path(pygdbmi.__file__).parent / 'IoManager.py').write_bytes(pathlib.Path('/tmp/IoManager.py').read_bytes())"

ENV IS_SANDBOX=1

RUN mkdir -p /root/.codex

COPY entrypoint.sh /root/entrypoint.sh
RUN chmod +x /root/entrypoint.sh

ENTRYPOINT ["/root/entrypoint.sh"]
WORKDIR /workspace
