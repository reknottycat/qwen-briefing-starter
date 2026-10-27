FROM node:22-bookworm-slim AS node
FROM python:3.11-slim-bookworm

COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && npm install --global --omit=dev @earendil-works/pi-coding-agent@1.4.0 \
    && npm cache clean --force

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY pyproject.toml README.md LICENSE THIRD_PARTY_NOTICES ./
COPY briefing ./briefing
RUN python -m pip install --no-cache-dir . \
    && groupadd --gid 1000 briefing \
    && useradd --uid 1000 --gid 1000 --create-home briefing \
    && mkdir -p /var/lib/briefing /var/lib/briefing-worker /var/lib/briefing-pi-settings \
    && chown briefing:briefing /var/lib/briefing /var/lib/briefing-worker /var/lib/briefing-pi-settings
COPY deploy/docker/entrypoint.py /usr/local/lib/briefing-entrypoint.py

USER 1000:1000
EXPOSE 8787
ENTRYPOINT ["python", "/usr/local/lib/briefing-entrypoint.py"]
CMD ["python", "-m", "uvicorn", "briefing.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8787", "--workers", "1", "--no-access-log"]
