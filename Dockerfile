FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl libpq-dev gcc && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e ".[dev]"
COPY src/ ./src/
RUN mkdir -p /app/tmp
EXPOSE 8080
CMD ["nwo-api", "serve"]
