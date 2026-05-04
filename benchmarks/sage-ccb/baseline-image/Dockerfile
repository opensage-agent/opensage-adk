FROM ghcr.io/opensage-agent/ctf_task_main:latest

RUN curl -fsSL https://claude.ai/install.sh | bash

# The container itself is the sandbox — tell `claude` so its anti-foot-gun
# check doesn't reject `--dangerously-skip-permissions` while running as root.
ENV IS_SANDBOX=1

RUN wget https://github.com/pwndbg/pwndbg/releases/download/2024.08.29/pwndbg_2024.08.29_amd64.deb && dpkg -i pwndbg_2024.08.29_amd64.deb
ENV PWNDBG_DISABLE_COLORS=1

RUN uv pip install mcp pygdbmi loguru

COPY --from=ghcr.io/opensage-agent/gdb_mcp:latest /app/gdb_mcp/gdb_mcp_server.py /app/gdb_mcp/gdb_mcp_server.py
COPY --from=ghcr.io/opensage-agent/gdb_mcp:latest /app/gdb_mcp/helper.py /app/gdb_mcp/helper.py
COPY --from=ghcr.io/opensage-agent/gdb_mcp:latest /app/.venv/lib/python3.12/site-packages/pygdbmi/IoManager.py /app/.venv/lib/python3.13/site-packages/pygdbmi/IoManager.py

COPY entrypoint.sh /root/entrypoint.sh
RUN chmod +x /root/entrypoint.sh

ENTRYPOINT ["/root/entrypoint.sh"]
WORKDIR /workspace
