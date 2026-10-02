FROM python:3.13-slim-bookworm
WORKDIR /jittersim
RUN pip install --no-cache-dir playwright==1.63.0 \
    && python -m playwright install --with-deps chromium
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .
COPY tests ./tests
CMD ["python", "-m", "tests.browser.journeys"]
