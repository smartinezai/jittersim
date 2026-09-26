# Runs the Linux datapath tests and the smoke check. Build from the repository root:
#   docker build -f tests/kernel/Dockerfile -t jittersim-kernel-tests .
#   docker run --rm --privileged jittersim-kernel-tests
FROM python:3.11-slim-bookworm
RUN apt-get update \
    && apt-get install -y --no-install-recommends iproute2 nftables iputils-ping unbound dnsutils hostapd conntrack \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /jittersim
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .
COPY tests ./tests
COPY deploy ./deploy
ENV JITTERSIM_KERNEL_TESTS=1
# The kernel tests, then the smoke check of the real apply, reset and firewall.
CMD ["sh", "-c", "python -m unittest discover -s tests/kernel -t . -v && python tests/integration/linux_network_smoke.py"]
