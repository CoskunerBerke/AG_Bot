FROM python:3.13-slim

WORKDIR /app

# Sistem bağımlılıkları ve saat dilimi ayarı
RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata \
    gcc \
    && rm -rf /var/lib/apt/lists/*

ENV TZ=America/New_York
ENV PYTHONUNBUFFERED=1
ENV PYTHONIOENCODING=utf-8

# Bağımlılıkları yükle
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Proje dosyalarını kopyala
COPY nasdaq_bot/ nasdaq_bot/
COPY state/learned/ state/learned/
COPY config.yaml main.py README.md ./

# Kalıcı veri dizinleri
RUN mkdir -p data logs reports state

# Varsayılan olarak canlı işlem botunu başlatır
CMD ["python", "main.py", "live"]
