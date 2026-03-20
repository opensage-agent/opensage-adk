FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

ENV JOERN_VERSION="v4.0.407" \
    JOERN_QUERYDB_VERSION="4.0.457" \
    SBT_VERSION="1.11.3" \
    LANG="C.UTF-8"

RUN apt update && \
    apt install -y wget git unzip curl

RUN curl -LsSf https://astral.sh/uv/install.sh | sh

RUN mkdir /app
WORKDIR /app


ENV PATH="/root/.local/bin:$PATH"

# Create a local venv and install deps into it.
RUN uv venv --python 3.12
ENV PATH="/app/.venv/bin:/root/.local/bin:$PATH"
RUN uv pip install httpx websockets setuptools

# install jdk21 sbt
RUN mkdir -p /data \
    && cd /data \
    && wget https://github.com/sbt/sbt/releases/download/v${SBT_VERSION}/sbt-${SBT_VERSION}.zip \
    && unzip *.zip \
    && rm *.zip \
    && mv sbt/ sbt-${SBT_VERSION}/ \
    && wget https://download.java.net/java/21/archive/jdk-21.0.7_linux-x64_bin.tar.gz \
    && tar zxvf *.tar.gz \
    && rm *.tar.gz

ENV JAVA_HOME=/data/jdk-21.0.7 \
    PATH="/data/sbt-${SBT_VERSION}/bin:/data/jdk-21.0.7/bin:$PATH"

RUN git clone https://github.com/joernio/joern.git /data/joern && \
    cd /data/joern && \
    git checkout ${JOERN_VERSION} && \
    sbt createDistribution && \
    unzip -qo -d /opt/joern /data/joern/target/joern-cli.zip && \
    cd /opt/joern/joern-cli && \
    ./joern-scan --updatedb --dbversion "${JOERN_QUERYDB_VERSION}"

ENV PATH="/opt/joern/joern-cli:$PATH"

EXPOSE 8081
WORKDIR /work

CMD ["joern", "--server", "--server-host", "0.0.0.0", "--server-port", "8081"]
