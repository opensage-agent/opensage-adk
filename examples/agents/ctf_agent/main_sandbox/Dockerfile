# Zero-day eval: Kali base + headless tooling + static analysis stack
ARG BASE_IMAGE=kalilinux/kali-rolling
FROM ${BASE_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive

# Base OS + common tooling
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
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
      python3-pip \
      python3-venv \
      python3-dev \
      qemu-system \
      qemu-system-x86 \
      qemu-user \
      qemu-user-static \
      z3 \
      ghidra \
      nodejs \
      npm && \
    (apt-get install -y ctags || apt-get install -y universal-ctags || apt-get install -y exuberant-ctags || true) && \
    (apt-get install -y radare2 || true) && \
    rm -rf /var/lib/apt/lists/*

# --- SageMath (binary install via conda-forge) ---
ENV MAMBA_ROOT_PREFIX=/opt/micromamba
RUN curl -Lso /usr/local/bin/micromamba \
      "https://github.com/mamba-org/micromamba-releases/releases/latest/download/micromamba-linux-64" && \
    chmod +x /usr/local/bin/micromamba && \
    micromamba create -y -n sage -c conda-forge sage && \
    micromamba clean -a -y && \
    ln -sf /opt/micromamba/envs/sage/bin/python /usr/local/bin/python3-sage

# --- uv / Python env ---
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
RUN mkdir -p /app
ENV PATH="/opt/micromamba/envs/sage/bin:/root/.local/bin:$PATH"
RUN uv venv --python python3 /app/.venv
ENV PATH="/app/.venv/bin:/opt/micromamba/envs/sage/bin:/root/.local/bin:$PATH"
RUN uv pip install --python /app/.venv/bin/python \
      "numpy>=1.26,<2" \
      neo4j \
      setuptools \
      angr \
      capstone \
      ROPGadget \
      lief \
      z3-solver \
      pyghidra-mcp \
      ghidramcp \
      playwright \
      "git+https://github.com/mrexodia/ida-pro-mcp.git"
RUN if [ -x /usr/share/ghidra/support/analyzeHeadless ]; then \
      ln -sf /usr/share/ghidra/support/analyzeHeadless /usr/local/bin/analyzeHeadless; \
    fi

# --- ida-pro ---
COPY --from=ghcr.io/opensage-agent/ida-pro:latest /opt/ida /opt/ida
COPY --from=ghcr.io/opensage-agent/ida-pro:latest /opt/lumina /opt/lumina
RUN cd /opt/ida && python idakeygen.py
COPY --from=ghcr.io/opensage-agent/ida-pro:latest /root/ida.reg /root/.idapro/
ENV PATH="/opt/ida:/opt/lumina:$PATH"

# Best-effort radare2 source install fallback when apt package is unavailable.
RUN if ! command -v r2 >/dev/null 2>&1; then \
      git clone --depth 1 https://github.com/radareorg/radare2.git /tmp/radare2 && \
      cd /tmp/radare2 && \
      ./sys/install.sh --without-pull --install && \
      rm -rf /tmp/radare2; \
    fi
# --- Playwright ---
RUN /app/.venv/bin/python -m playwright install chromium

ENTRYPOINT []
WORKDIR /workspace
