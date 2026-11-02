FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# LibreOffice (Word -> PDF), Thai fallback font, fontconfig
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libreoffice-writer-nogui fonts-tlwg-laksaman-ttf fontconfig poppler-utils \
 && rm -rf /var/lib/apt/lists/*

# Agency fonts (TH SarabunIT๙ / TH Sarabun New) from fonts/, plus fallback mapping
COPY fonts/ /usr/share/fonts/truetype/agency/
COPY docker/65-th-sarabun-fallback.conf /etc/fonts/conf.d/65-th-sarabun-fallback.conf
RUN fc-cache -f && (fc-list | grep -i sarabun || echo "WARNING: TH Sarabun fonts not found in fonts/ - using Laksaman fallback")

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
RUN DJANGO_SECRET_KEY=build python manage.py collectstatic --noinput -v0 \
 && useradd --uid 1000 --create-home --home-dir /home/app app \
 && mkdir -p /app/media /app/data \
 && chown -R app:app /app/media /app/data \
 && chmod +x docker/entrypoint.sh

USER app
# writable HOME: gunicorn control socket and the LibreOffice profile live here
ENV HOME=/home/app
EXPOSE 8000
ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["web"]
