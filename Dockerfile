FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=America/Caracas \
    DATABASE_URL=sqlite:////data/ttlink.db

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Usuario sin privilegios; /data es el volumen de la base SQLite
RUN useradd -m -u 1000 appuser && mkdir -p /data && chown -R appuser:appuser /app /data
USER appuser

EXPOSE 8000

# --workers 1: SQLite y el token OAuth de TTLock en memoria no soportan
# varios procesos. --forwarded-allow-ips='*': para registrar la IP real del
# huésped detrás de Caddy (seguro: el contenedor no publica puertos).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*", "--workers", "1"]
