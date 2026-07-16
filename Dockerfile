FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MT5_MANAGER_EXPORT_MODE=download

WORKDIR /app

COPY pyproject.toml README.md ./
COPY mt5_manager ./mt5_manager
COPY portfolio_manager ./portfolio_manager
COPY ubs ./ubs
COPY assets ./assets

RUN pip install --no-cache-dir .

EXPOSE 8750

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8750/', timeout=3)" || exit 1

CMD ["python", "-m", "mt5_manager.docker_entrypoint"]
