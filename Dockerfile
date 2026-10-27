# Dashboards for a Samsung The Frame, driven by Home Assistant.
# Build:  docker build -t tvdash .
# Run:    see deploy/docker-compose.example.yml or charts/tvdash
FROM python:3.12-slim-bookworm

# The release workflow overrides these through docker/metadata-action; the source label links the
# package on ghcr.io to the repository.
LABEL org.opencontainers.image.title="tvdash" \
      org.opencontainers.image.description="Dashboards as art on a Samsung The Frame, driven by Home Assistant"

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Europe/Amsterdam \
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright \
    TVDASH_BROWSER_CHANNEL="" \
    TVDASH_STATE=/data \
    TVDASH_OUT=/tmp/tvdash

# tzdata: the board works in Europe/Amsterdam. Inter + Noto Color Emoji: the fonts the design is made with.
# git is only needed to install the Samsung library from GitHub and is removed again.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git tzdata fonts-inter fonts-noto-color-emoji \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt \
 && playwright install --with-deps chromium \
 && apt-get purge -y --auto-remove git \
 && rm -rf /var/lib/apt/lists/*

COPY tv.py tvdash.py morning.py server.py ./
COPY dashboard ./dashboard

# Runs as a normal user; /data holds the TV pairing token and "what to go back to" (mount a volume there).
RUN useradd --create-home --uid 1000 tvdash && mkdir /data /tmp/tvdash && chown tvdash /data /tmp/tvdash
USER 1000:1000   # numeric, so Kubernetes can verify runAsNonRoot
VOLUME /data
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=4)"

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8080"]
