FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# LibreOffice (Word -> PDF), Thai fallback font, fontconfig
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libreoffice-writer-nogui fonts-tlwg-laksaman-ttf fontconfig poppler-utils \
 && rm -rf /var/lib/apt/lists/*

# Agency fonts (TH SarabunIT๙ / TH Sarabun New) from fonts/ win; otherwise "Sarabun Compat"
# (Laksaman shrunk to TH Sarabun's metrics, built here - see tools/make_fallback_font.py)
COPY fonts/ /usr/share/fonts/truetype/agency/
COPY tools/make_fallback_font.py /tmp/make_fallback_font.py
RUN pip install --no-cache-dir fonttools==4.68.0 \
 && python /tmp/make_fallback_font.py /usr/share/fonts/truetype/tlwg /usr/share/fonts/truetype/sarabun-compat \
 && pip uninstall -y fonttools && rm /tmp/make_fallback_font.py
COPY docker/90-th-sarabun-fallback.conf /etc/fonts/conf.d/90-th-sarabun-fallback.conf
RUN fc-cache -f && (fc-list | grep -i "SarabunIT" || echo "NOTE: TH SarabunIT๙ not in fonts/ - PDFs use the Sarabun Compat fallback") \
 && fc-match "TH SarabunIT๙"

WORKDIR /app
COPY requirements.txt requirements-server.txt ./
RUN pip install -r requirements-server.txt

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
