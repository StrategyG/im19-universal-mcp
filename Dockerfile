FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml README.md requirements.lock ./
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./alembic.ini
RUN pip install --no-cache-dir --require-hashes -r requirements.lock \
    && pip install --no-cache-dir --no-deps .
RUN useradd --create-home --uid 10001 im19
USER im19
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=15s --start-period=20s --retries=3 \
    CMD ["python", "-m", "im19_mcp.healthcheck"]
CMD ["im19-gateway"]

