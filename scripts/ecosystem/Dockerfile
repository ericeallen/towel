# The ecosystem corpus runs 141 third-party test suites with the invoking
# user's privileges. It belongs in a container, not on a workstation.
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        git build-essential curl ca-certificates pkg-config \
        libxml2-dev libxslt1-dev zlib1g-dev libjpeg-dev libfreetype6-dev \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# The harness builds a per-project environment with uv.
RUN pip install --no-cache-dir uv

# Towel's own dependencies, so the harness can import it from the mounted source.
RUN pip install --no-cache-dir "mypy" "pyright" "black" "isort" "tomli" "tqdm"

# The candidate itself, for its metadata only: the harness runs Towel from the
# source it is given and hands each subprocess a scratch HOME, so the package
# must be on the system path rather than a user one. `cli.py` reads its own
# version through importlib.metadata and cannot start without it.
# Build the wheel first and copy it in beside this file:
#     python -m build && cp dist/code_towel-*.whl scripts/ecosystem/
ARG WHEEL=code_towel-*.whl
COPY ${WHEEL} /tmp/
RUN pip install --no-cache-dir --no-deps /tmp/*.whl && rm /tmp/*.whl

RUN useradd --create-home --shell /bin/bash runner
USER runner
WORKDIR /home/runner
