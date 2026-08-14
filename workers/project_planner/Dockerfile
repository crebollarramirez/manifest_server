FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

WORKDIR /app

COPY workers/project_planner/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY workers/commons /app/workers/commons
COPY workers/indexer/indexer /app/workers/indexer/indexer
COPY workers/project_planner/project_planner /app/workers/project_planner/project_planner
COPY workers/project_planner/project_planner_worker.py /app/workers/project_planner/project_planner_worker.py

CMD ["python", "workers/project_planner/project_planner_worker.py"]
