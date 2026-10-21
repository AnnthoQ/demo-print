# Opcional: servidor de monitoreo en contenedor (solo biblioteca estándar de Python).
#   docker build -t monitor-impresoras .
#   docker run -d --name monitor-impresoras -p 8787:8787 --env-file .env \
#     -v "$PWD/config:/app/config" monitor-impresoras
FROM python:3.12-slim

WORKDIR /app
COPY server/ server/
COPY config/printers.example.json config/trays.json config/

ENV PYTHONIOENCODING=utf-8 PYTHONDONTWRITEBYTECODE=1 HOST=0.0.0.0 PORT=8787
EXPOSE 8787
RUN useradd --system --uid 10001 monitor && chown -R monitor /app/config
USER monitor

HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8787/health', timeout=2).status == 200 else 1)"
CMD ["python", "server/servidor.py"]
