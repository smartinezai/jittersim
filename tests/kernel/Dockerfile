# Runs the Linux datapath tests. Build from the repository root:
#   docker build -f tests/kernel/Dockerfile -t jittersim-kernel-tests .
#   docker run --rm --privileged jittersim-kernel-tests
FROM python:3.11-slim-bookworm
RUN apt-get update \
    && apt-get install -y --no-install-recommends iproute2 nftables iputils-ping \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /jittersim
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .
COPY tests ./tests
ENV JITTERSIM_KERNEL_TESTS=1
CMD ["python", "-m", "unittest", "discover", "-s", "tests/kernel", "-t", ".", "-v"]
