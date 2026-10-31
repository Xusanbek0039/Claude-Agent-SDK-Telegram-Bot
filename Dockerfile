FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# app/ (shriftlar bilan), avatar_cache/ va (bo'lsa) husanbek/ rasmlari
COPY . .

ENV PYTHONUNBUFFERED=1 \
    MEDIA_DIR=/srv/media

CMD ["python", "bot.py"]
