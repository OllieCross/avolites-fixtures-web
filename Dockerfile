FROM python:3.12-slim-trixie

RUN apt-get update \
    && apt-get install -y --no-install-recommends 7zip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN useradd -u 1000 -m app && mkdir /data && chown app:app /data
COPY app ./app
USER app
ENV DATA_DIR=/data PYTHONUNBUFFERED=1
VOLUME /data

CMD ["uvicorn", "app.web:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
