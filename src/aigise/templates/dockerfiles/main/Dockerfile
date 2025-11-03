ARG BASE_IMAGE=n132/arvo:29728-vul
FROM ${BASE_IMAGE}

ENV PATH="/root/.local/bin:$PATH"

RUN apt-get update && apt-get install -y curl ctags && \
    curl -LsSf https://astral.sh/uv/install.sh | sh && \
    uv python install 3.12 && \
    PYDIR=$(find /root/.local/share/uv/python -type d -name "cpython-3.12*" | head -n1) && \
    ln -sf $PYDIR/bin/python /usr/local/bin/python && \
    ln -sf $PYDIR/bin/python3 /usr/local/bin/python3 && \
    ln -sf $PYDIR/bin/pip /usr/local/bin/pip && \
    ln -sf $PYDIR/bin/pip3 /usr/local/bin/pip3 && \
    python --version && pip --version && \
    rm -rf /var/lib/apt/lists/*
