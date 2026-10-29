FROM python:3.12-slim-bookworm@sha256:e697c81897302a4c4333bade000d98038fbb21eb064f8ee1fd42d02ebcd9d337
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update && apt-get install -y --no-install-recommends git build-essential libmagic1 && rm -rf /var/lib/apt/lists/*
# The sibling Dockerfile.dockerignore keeps repository workflow fixtures available to pytest.
COPY . /src
WORKDIR /src
RUN python -m pip install --no-cache-dir -e '.[ocr]' pytest
RUN python -m compileall -q app tools
CMD ["python", "-m", "pytest", "-q"]
