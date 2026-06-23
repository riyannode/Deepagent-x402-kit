FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SDK_PROJECT_ROOT=/app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates gnupg \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_22.x nodistro main" > /etc/apt/sources.list.d/nodesource.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY package.json package-lock.json ./
COPY src ./src
COPY scripts ./scripts
COPY docker-entrypoint.sh ./docker-entrypoint.sh

RUN pip install --upgrade pip \
    && pip install -e . \
    && npm ci --omit=dev \
    && chmod +x /app/docker-entrypoint.sh

# L3: Non-root user for production safety
RUN useradd -m -r -s /bin/false appuser \
    && chown -R appuser:appuser /app \
    && mkdir -p /data && chown -R appuser:appuser /data

VOLUME ["/data"]
USER appuser
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["doctor"]
