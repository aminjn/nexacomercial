FROM python:3.11-slim
WORKDIR /srv
COPY requirements.txt .
# Optional PyPI mirror:  docker compose build --build-arg PIP_INDEX_URL=https://...
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN pip install --no-cache-dir --index-url "$PIP_INDEX_URL" -r requirements.txt
COPY app ./app
ENV APP_DATA_DIR=/data
VOLUME ["/data"]
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
