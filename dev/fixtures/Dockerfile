# Fixture generator only; not part of the indexer image
FROM debian:trixie-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libreoffice-writer-nogui libreoffice-calc-nogui libreoffice-impress-nogui \
      poppler-utils img2pdf fonts-dejavu-core python3-msoffcrypto-tool \
 && rm -rf /var/lib/apt/lists/*
