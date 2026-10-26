FROM debian:trixie-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      python3 python3-psycopg poppler-utils catdoc \
      ocrmypdf tesseract-ocr-nld tesseract-ocr-eng \
 && rm -rf /var/lib/apt/lists/*
COPY indexer.py /usr/local/bin/indexer.py
ENV PYTHONUNBUFFERED=1 OMP_THREAD_LIMIT=1
USER 33:33
ENTRYPOINT ["python3", "/usr/local/bin/indexer.py"]
