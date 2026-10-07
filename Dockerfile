FROM python:3.11-slim
WORKDIR /srv
COPY requirements.txt .
# Optional PyPI mirror:  docker compose build --build-arg PIP_INDEX_URL=https://...
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN pip install --no-cache-dir --default-timeout=120 --retries 10 --index-url "$PIP_INDEX_URL" -r requirements.txt
# Xray-core for the built-in v2ray client. If GitHub is unreachable the build still succeeds and
# Xray can be installed later from the settings page. Mirror:  --build-arg XRAY_URL=https://.../Xray-linux-64.zip
ARG XRAY_URL=
COPY app/xray_get.py /tmp/xray_get.py
RUN python /tmp/xray_get.py /usr/local/bin "$XRAY_URL"
# Chromium + Playwright for "log in as a user" accounts (<platform>_web) — a few hundred MB, so only on request:
#   INSTALL_BROWSER=1 APT_MIRROR=https://mirror.arvancloud.ir docker compose up -d --build
# Kept after the layers above so a normal update reuses them from the cache. If it fails the build still
# succeeds; only the browser accounts won't work.
ARG INSTALL_BROWSER=0
ARG APT_MIRROR=
RUN [ "$INSTALL_BROWSER" = "1" ] || exit 0; \
    if [ -n "$APT_MIRROR" ]; then \
      sed -i "s|http://deb.debian.org|$APT_MIRROR|g" /etc/apt/sources.list.d/*.sources /etc/apt/sources.list 2>/dev/null || true; fi; \
    (pip install --no-cache-dir --index-url "$PIP_INDEX_URL" "playwright>=1.45" \
     && apt-get update -o Acquire::Retries=3 \
     && apt-get install -y --no-install-recommends chromium fonts-noto-core fonts-noto-color-emoji \
     && rm -rf /var/lib/apt/lists/*) \
    || echo "!! browser install failed — browser accounts (*_web) will not work until it is installed"
COPY app ./app
ENV APP_DATA_DIR=/data
VOLUME ["/data"]
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
