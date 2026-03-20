ARG BASE_IMAGE=ubuntu:20.04
FROM ${BASE_IMAGE}

RUN apt-get update && apt-get install -y curl python3 wget

RUN mkdir /app
WORKDIR /app

RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

COPY ./artifacts/pdb_mcp_server.py /app/pdb_mcp/pdb_mcp_server.py

# install dependencies
RUN uv venv --python 3.12
RUN uv pip install mcp python-dotenv loguru

# Expose port for MCP server
EXPOSE 1112

CMD ["uv","run","-m","pdb_mcp.pdb_mcp_server"]
