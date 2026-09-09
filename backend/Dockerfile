# SATVA backend image.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# libgl/libglib are needed by OpenCV even in the headless build; libpq for psycopg.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        libgl1 \
        libglib2.0-0 \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first so an application edit does not invalidate the layer.
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run as a non-root user: the API accepts uploads, and a container that writes
# files should not be doing so as root.
RUN useradd --create-home --uid 10001 satva \
    && chown -R satva:satva /app
USER satva

EXPOSE 8000

# Deliberately no HEALTHCHECK here. This one image runs four different services
# (api, worker, beat, mqtt-bridge) and only the api serves HTTP, so an
# image-level HTTP probe would mark the other three permanently unhealthy and
# force each of them to override it back off. The api declares its own probe in
# docker-compose.yml, where the service that needs it lives.

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
