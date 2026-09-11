# syntax=docker/dockerfile:1.7
FROM python:3.12-slim-bookworm

ARG SIMPLEOFFICE_UID=10001
ARG SIMPLEOFFICE_GID=10001

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH=/opt/simpleoffice4me/.venv/bin:$PATH \
    SIMPLEOFFICE_DOCUMENT_ROOT=/var/lib/simpleoffice4me/documents \
    SIMPLEOFFICE_MINI_SERVICES_CONFIG=/var/lib/simpleoffice4me/instance/mini-services.json

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        cups-client \
        git \
        gosu \
        iproute2 \
        nftables \
        procps \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid "${SIMPLEOFFICE_GID}" simpleoffice \
    && useradd --uid "${SIMPLEOFFICE_UID}" --gid simpleoffice --home-dir /var/lib/simpleoffice4me --shell /usr/sbin/nologin simpleoffice

WORKDIR /opt/simpleoffice4me
COPY . /opt/simpleoffice4me

RUN python -m venv /opt/simpleoffice4me/.venv \
    && /opt/simpleoffice4me/.venv/bin/pip install --no-cache-dir . \
    && rm -rf /opt/simpleoffice4me/database /opt/simpleoffice4me/instance \
    && install -d -o simpleoffice -g simpleoffice -m 0750 \
        /var/lib/simpleoffice4me \
        /var/lib/simpleoffice4me/database \
        /var/lib/simpleoffice4me/documents \
        /var/lib/simpleoffice4me/instance \
    && ln -s /var/lib/simpleoffice4me/database /opt/simpleoffice4me/database \
    && ln -s /var/lib/simpleoffice4me/instance /opt/simpleoffice4me/instance \
    && chmod 0755 /opt/simpleoffice4me/deploy/docker/entrypoint.sh \
    && chown -R root:root /opt/simpleoffice4me \
    && chown -R simpleoffice:simpleoffice /var/lib/simpleoffice4me

VOLUME ["/var/lib/simpleoffice4me"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import os,urllib.request; p='/api/error-reports/v1/health' if os.environ.get('SIMPLEOFFICE_CONTAINER_ROLE')=='error-relay' else '/'; urllib.request.urlopen('http://127.0.0.1:8080'+p, timeout=4).read(1)" || exit 1

ENTRYPOINT ["/opt/simpleoffice4me/deploy/docker/entrypoint.sh"]
CMD ["python", "-m", "tools.launcher", "start"]
