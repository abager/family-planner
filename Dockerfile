# Familieplan-server. Aula-biblioteket kræver Python 3.14.
FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 TZ=Europe/Copenhagen
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . /app

# Kører som almindelig bruger. /data er den eneste mappe, der gemmer noget: config.toml, secrets/ (Aula-login) og de hentede data.
RUN useradd --create-home --uid 1000 app && mkdir -p /data && chown app:app /data
USER app
WORKDIR /data
VOLUME /data
EXPOSE 8080

HEALTHCHECK --interval=60s --timeout=5s --start-period=40s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=4)"

CMD ["python", "/app/server.py", "--config", "/data/config.toml", "--host", "0.0.0.0", "--port", "8080"]
