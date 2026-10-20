FROM docker:27-cli AS docker-cli

FROM alpine:3.22 AS github-cli
ARG TARGETARCH
ARG GH_VERSION=2.94.0
RUN apk add --no-cache ca-certificates curl tar \
    && curl -fsSL "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_${TARGETARCH}.tar.gz" \
       | tar -xz -C /tmp \
    && install -m 0755 "/tmp/gh_${GH_VERSION}_linux_${TARGETARCH}/bin/gh" /usr/local/bin/gh

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MT5_MANAGER_EXPORT_MODE=download

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker-cli /usr/local/libexec/docker/cli-plugins/docker-compose /usr/local/libexec/docker/cli-plugins/docker-compose
COPY --from=github-cli /usr/local/bin/gh /usr/local/bin/gh

COPY pyproject.toml README.md ./
COPY mt5_manager ./mt5_manager
COPY portfolio_manager ./portfolio_manager
COPY ubs ./ubs
COPY assets ./assets

RUN pip install --no-cache-dir .

EXPOSE 8750

# El manager sirve https cuando encuentra su certificado en /app/runtime/tls,
# asi que el chequeo mira el esquema antes de llamar. No valida el certificado:
# comprueba que el proceso contesta, no la confianza, y el nombre con el que se
# llama a si mismo no tiene por que estar en el.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import ssl, urllib.request; from mt5_manager import manager_tls; s=manager_tls.paths({'tls_dir': '/app/runtime/tls'}, None) is not None; urllib.request.urlopen(('https' if s else 'http')+'://127.0.0.1:8750/', timeout=3, context=ssl._create_unverified_context() if s else None)" || exit 1

CMD ["python", "-m", "mt5_manager.docker_entrypoint"]
