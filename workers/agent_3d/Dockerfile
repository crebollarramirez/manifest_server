FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

WORKDIR /app

COPY workers/agent_3d/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY workers/commons /app/workers/commons
COPY workers/indexer/indexer /app/workers/indexer/indexer
COPY workers/agent_3d/__init__.py /app/workers/agent_3d/__init__.py
COPY workers/agent_3d/failures.py /app/workers/agent_3d/failures.py
COPY workers/agent_3d/orchestrator.py /app/workers/agent_3d/orchestrator.py
COPY workers/agent_3d/repository.py /app/workers/agent_3d/repository.py
COPY workers/agent_3d/planning /app/workers/agent_3d/planning
COPY workers/agent_3d/tools /app/workers/agent_3d/tools
COPY workers/agent_3d/edit_worker.py /app/workers/agent_3d/edit_worker.py

CMD ["python", "workers/agent_3d/edit_worker.py"]
