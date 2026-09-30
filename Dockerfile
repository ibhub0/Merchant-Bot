FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# gcc is only needed if tgcrypto has no prebuilt wheel for your platform
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Webhook server for the Deku SMS forwarder (config.py reads PORT)
ENV PORT=8080
EXPOSE 8080

CMD ["python", "main.py"]
