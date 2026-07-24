FROM condaforge/miniforge3:latest

WORKDIR /app

COPY environment.yml /app/environment.yml
RUN mamba env create -f /app/environment.yml && mamba clean -afy

ENV PATH=/opt/conda/envs/validator/bin:$PATH
ENV PYTHONUNBUFFERED=1

COPY cad_ast_validator.py /app/cad_ast_validator.py
COPY cad_validation_runner.py /app/cad_validation_runner.py
COPY cad_validation_worker.py /app/cad_validation_worker.py
COPY cadquery_runtime.py /app/cadquery_runtime.py
COPY validate_cad_job.py /app/validate_cad_job.py

RUN python -c "import cadquery; import cad_ast_validator"

CMD ["python", "cad_validation_worker.py"]
