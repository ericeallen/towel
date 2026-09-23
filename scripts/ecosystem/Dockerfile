# The ecosystem corpus runs 141 third-party test suites with the invoking
# user's privileges. It belongs in a container, not on a workstation.
FROM python:3.12-slim

# pyright runs on Node. Without a node on PATH its wrapper downloads one into
# HOME on first use, and the harness gives every worker a scratch HOME.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git build-essential curl ca-certificates pkg-config nodejs \
        libxml2-dev libxslt1-dev zlib1g-dev libjpeg-dev libfreetype6-dev \
    && rm -rf /var/lib/apt/lists/*

# The harness builds each project's environment with uv, and runs Towel there.
# Nothing of Towel's goes into this interpreter, which only runs the harness:
# Towel's tools here would stand in for the project's environment, keeping the
# project's dependencies from the type checker and putting their own copies of
# click, packaging and the rest in front of the projects of those names.
RUN pip install --no-cache-dir uv

# The candidate, which the harness installs into every project's environment
# (--towel-wheel /opt/towel) after checking that it is the source under
# --towel-src. Build the wheel first and copy it in beside this file:
#     uv build --wheel --out-dir dist && cp dist/code_towel-*.whl scripts/ecosystem/
ARG WHEEL=code_towel-*.whl
COPY ${WHEEL} /opt/towel/

RUN useradd --create-home --shell /bin/bash runner
USER runner
WORKDIR /home/runner
