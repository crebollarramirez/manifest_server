FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

WORKDIR /app

COPY workers/cad_editor/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY workers/indexer/indexer /app/workers/indexer/indexer
COPY workers/cad_editor/cad_editor /app/workers/cad_editor/cad_editor
COPY workers/cad_editor/tool_worker.py /app/workers/cad_editor/tool_worker.py

CMD ["python", "workers/cad_editor/tool_worker.py"]
