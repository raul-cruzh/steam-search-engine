FROM python:3.12-slim-trixie

ARG PYLUCENE_VERSION=10.0.0

# Install Java and compilation dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    openjdk-21-jdk-headless \
    build-essential \
    curl \
    ca-certificates \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Configure Java
ENV JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64
ENV JCC_JDK=${JAVA_HOME}
ENV PATH="${JAVA_HOME}/bin:${PATH}"
ENV LD_LIBRARY_PATH="${JAVA_HOME}/lib:${JAVA_HOME}/lib/server"

# Install Python build dependencies
RUN pip install --no-cache-dir setuptools==70.3.0 wheel

# Download official Apache PyLucene source code
WORKDIR /tmp/pylucene

RUN curl -fSL https://downloads.apache.org/lucene/pylucene/pylucene-${PYLUCENE_VERSION}-src.tar.gz -o pylucene.tar.gz \
    && tar -xzf pylucene.tar.gz --strip-components=1 \
    && rm pylucene.tar.gz

# Build and install JCC
ENV JCC_LFLAGS="-L${JAVA_HOME}/lib:-ljava:-L${JAVA_HOME}/lib/server:-ljvm:-Wl,-S"

RUN cd jcc && NO_SHARED=1 python setup.py install
# Build and install PyLucene
RUN make all install \
    PYTHON=python \
    JCC="python -m jcc" \
    NUM_FILES=16

# Verify installation
RUN python -c "import lucene; lucene.initVM(); print(lucene.VERSION)"

# Remove compilation files
WORKDIR /app
RUN rm -rf /tmp/pylucene