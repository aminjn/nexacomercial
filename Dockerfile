FROM python:3.11-slim
WORKDIR /srv
COPY requirements.txt .
# Optional PyPI mirror:  docker compose build --build-arg PIP_INDEX_URL=https://...
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN pip install --no-cache-dir --index-url "$PIP_INDEX_URL" -r requirements.txt
# Xray-core for the built-in v2ray client. If GitHub is unreachable the build still succeeds and
# Xray can be installed later from the settings page. Mirror:  --build-arg XRAY_URL=https://.../Xray-linux-64.zip
ARG XRAY_URL=
COPY app/xray_get.py /tmp/xray_get.py
RUN python /tmp/xray_get.py /usr/local/bin "$XRAY_URL"
COPY app ./app
ENV APP_DATA_DIR=/data
VOLUME ["/data"]
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
