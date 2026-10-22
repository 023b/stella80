# One image for both the web app and the vault agent (compose.yaml runs it twice with different commands).
FROM python:3.14-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends poppler-utils ghostscript git \
 && rm -rf /var/lib/apt/lists/* \
 && git config --system --add safe.directory '*'

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY stella80 stella80
COPY agent agent

ARG UID=1000
ARG GID=1000
RUN groupadd -g $GID stella && useradd -u $UID -g $GID -m stella
USER stella

ENV PYTHONUNBUFFERED=1 STELLA80_CONFIG=/app/stella80.yaml HOST=0.0.0.0
EXPOSE 8480
CMD ["python", "stella80/app.py"]
