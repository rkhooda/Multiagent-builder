# syntax=docker/dockerfile:1

# Same trick backend/Dockerfile uses for `node`: copy a static binary out of
# its official image rather than apt-installing a package (no dpkg lock, no
# extra apt layer, deterministic across rebuilds).
FROM docker:27-cli AS dockercli

FROM python:3.11-slim

COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker
RUN docker --version

WORKDIR /app
COPY backend/sandbox/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/sandbox/ .

EXPOSE 8100
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8100"]
