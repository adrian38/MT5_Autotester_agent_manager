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

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8750/', timeout=3)" || exit 1

CMD ["python", "-m", "mt5_manager.docker_entrypoint"]
