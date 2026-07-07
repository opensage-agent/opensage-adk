FROM cruizba/ubuntu-dind:noble-latest@sha256:ef92362b4dbd3b0bd67119cded51247da91dc236103605787e2d50c1d6f1d7ff

RUN DEBIAN_FRONTEND=noninteractive && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
    autoconf \
    automake \
    bear \
    build-essential \
    clang \
    clang-16 \
    clangd \
    cmake \
    curl \
    gdb \
    git \
    libclang-rt-dev \
    lld \
    llvm \
    make \
    pkg-config \
    python3 \
    python3-pip \
    python3.12-venv \
    ssh \
    sudo \
    tmux \
    wget && \
    install -m 0755 -d /etc/apt/keyrings && \
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc && \
    chmod a+r /etc/apt/keyrings/docker.asc && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
    tee /etc/apt/sources.list.d/docker.list > /dev/null && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
    docker-ce \
    docker-ce-cli \
    containerd.io \
    docker-buildx-plugin \
    docker-compose-plugin && \
    python3 -m venv /opt/venv && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

ENV PATH="/opt/venv/bin:$PATH"

RUN cd /tmp && \
    wget https://github.com/universal-ctags/ctags/releases/download/v6.2.0/universal-ctags-6.2.0.tar.gz && \
    tar -xzf universal-ctags-6.2.0.tar.gz && \
    cd universal-ctags-6.2.0 && \
    ./autogen.sh && \
    ./configure --prefix=/usr/local && \
    make && \
    make install && \
    cd / && \
    rm -rf /tmp/universal-ctags-6.2.0*

WORKDIR /source
COPY patchagent /source/patchagent
COPY pyproject.toml /source/pyproject.toml

RUN pip install --no-cache-dir -e ".[dev]"
