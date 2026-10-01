FROM python:3.11-slim
RUN apt-get update -qq && apt-get install -y --no-install-recommends \
    iproute2 iputils-ping nftables nodejs network-manager dbus tcpdump \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY deploy ./deploy
COPY tests ./tests
RUN pip install '.[dev]'
CMD ["python", "-m", "pytest", "-q"]
