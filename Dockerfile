# syntax=docker/dockerfile:1.7

FROM node:22-alpine AS frontend-builder

WORKDIR /build/frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build


FROM python:3.11-slim-bookworm AS runtime

ARG APP_UID=10001
ARG APP_GID=10001

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/home/voiceagent

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates libgomp1 tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid "${APP_GID}" voiceagent \
    && useradd \
        --uid "${APP_UID}" \
        --gid "${APP_GID}" \
        --create-home \
        --shell /usr/sbin/nologin \
        voiceagent

WORKDIR /app

COPY backend/requirements.txt ./backend/requirements.txt
RUN python -m pip install --upgrade pip \
    && python -m pip install --requirement backend/requirements.txt

COPY --chown=voiceagent:voiceagent backend/ ./backend/
COPY --from=frontend-builder --chown=voiceagent:voiceagent /build/frontend/dist/ ./frontend/dist/

RUN mkdir -p /app/data \
    && chown -R voiceagent:voiceagent /app/data /home/voiceagent

USER voiceagent

EXPOSE 8000

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "uvicorn", "backend.app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
