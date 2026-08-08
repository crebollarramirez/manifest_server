FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

WORKDIR /app

COPY workers/indexer/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY workers/commons /app/workers/commons
COPY workers/indexer/indexer /app/workers/indexer/indexer
COPY workers/indexer/index_worker.py /app/workers/indexer/index_worker.py

CMD ["python", "workers/indexer/index_worker.py"]
