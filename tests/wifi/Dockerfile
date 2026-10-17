FROM python:3.11-slim
RUN apt-get update -qq && apt-get install -y --no-install-recommends \
    iproute2 iputils-ping nftables nodejs network-manager dbus tcpdump \
    git openssh-client openssl curl sudo dnsmasq-base unbound dnsutils \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system jittersim
WORKDIR /app
COPY pyproject.toml ./
# The docs link test follows relative links to the other top-level pages (ROADMAP.md, DESIGN.md ...).
COPY *.md ./
COPY src ./src
COPY deploy ./deploy
COPY tests ./tests
# The local CI and release tests read the scripts, the docs and the workflow files.
COPY scripts ./scripts
COPY docs ./docs
COPY .github ./.github
COPY experiments/homepage-bounce-reference/fixture.py ./experiments/homepage-bounce-reference/fixture.py
RUN pip install '.[dev]'
CMD ["python", "-m", "pytest", "-q"]
