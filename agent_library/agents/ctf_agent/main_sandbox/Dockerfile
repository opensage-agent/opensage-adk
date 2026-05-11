# Zero-day eval: Kali base + headless tooling + static analysis stack
ARG BASE_IMAGE=kalilinux/kali-last-release
FROM ${BASE_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive

# Base OS + common tooling
RUN apt-get -qq update && \
    apt-get -qq install -y --no-install-recommends \
      kali-linux-headless \
      ca-certificates \
      curl \
      wget \
      git \
      unzip \
      bzip2 \
      zstd \
      jq \
      ripgrep \
      build-essential \
      pkg-config \
      file \
      libssl-dev \
      libmagic-dev \
      libuv1-dev \
      libzip-dev \
      openjdk-21-jdk-headless \
      python3 \
      python3-venv \
      python3-dev \
      qemu-system \
      qemu-system-x86 \
      qemu-user \
      qemu-user-static \
      z3 \
      nodejs \
      npm \
      universal-ctags \
      radare2 \
      gfortran \
      openssl \
      patch \
      libz-dev \
      libbz2-dev \
      perl \
      libboost-dev \
      libncurses-dev \
      libreadline-dev \
      libcrypt-dev && \
      apt-get -qq clean && \
    rm -rf /var/lib/apt/lists/*

# --- uv / Python env ---
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"
RUN mkdir -p /app
RUN uv venv --python python3 /app/.venv
ENV VIRTUAL_ENV=/app/.venv
ENV PATH="/app/.venv/bin:$PATH"
RUN uv pip install --python /app/.venv/bin/python \
      "protobuf==3.20.0" \
      "numpy>=1.26,<2" \
      scipy \
      pillow \
      neo4j \
      setuptools \
      angr \
      capstone \
      ROPGadget \
      lief \
      z3-solver \
      mcp \
      requests \
      playwright \
      pwntools \
      chepy \
      "git+https://github.com/ziyue-pan/pyghidra-mcp.git" \
      "https://github.com/mrexodia/ida-pro-mcp/archive/refs/heads/main.zip"


# --- Playwright ---
RUN /app/.venv/bin/python -m playwright install chromium

# --- ida-pro + ida-pro-mcp ---
COPY --from=ghcr.io/opensage-agent/ida-pro:latest /opt/ida /opt/ida
COPY --from=ghcr.io/opensage-agent/ida-pro:latest /opt/lumina /opt/lumina
RUN cd /opt/ida && python idakeygen.py && \
    cd idalib/python && uv pip install --python /app/.venv/bin/python . && \
    python py-activate-idalib.py -d /opt/ida
COPY --from=ghcr.io/opensage-agent/ida-pro:latest /root/ida.reg /root/.idapro/
ENV PATH="/opt/ida:/opt/lumina:$PATH"

# --- ghidra + ghidra-mcp ---
COPY --from=ghcr.io/opensage-agent/ghidra:latest /opt/ghidra /opt/ghidra
COPY --from=ghcr.io/opensage-agent/ghidra:latest /build/target/GhidraMCP-*.jar /opt/GhidraMCP.jar

# --- sagemath ---
COPY --from=ghcr.io/opensage-agent/sagemath:latest /opt/sagemath /opt/sagemath
ENV PATH="$PATH:/opt/sagemath/bin"

# --- Entry ---
COPY entrypoint.sh /root/entrypoint.sh
RUN chmod +x /root/entrypoint.sh

# --- Expose MCP ports ---
EXPOSE 8002
EXPOSE 8003
EXPOSE 8004

ENTRYPOINT ["/root/entrypoint.sh"]
WORKDIR /workspace
