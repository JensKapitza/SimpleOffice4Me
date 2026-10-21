FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update && apt-get install -y --no-install-recommends git build-essential libmagic1 && rm -rf /var/lib/apt/lists/*
COPY . /src
WORKDIR /src
RUN python -m pip install --no-cache-dir -e '.[ocr]' pytest
RUN python -m compileall -q app tools || true
CMD ["python", "-m", "pytest", "-q"]
