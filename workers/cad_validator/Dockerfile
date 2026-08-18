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
COPY subprocess_sandbox.py /app/subprocess_sandbox.py
COPY geometry /app/geometry
COPY geometry_inspection.py /app/geometry_inspection.py
COPY geometry_comparison.py /app/geometry_comparison.py
COPY geometry_check_runner.py /app/geometry_check_runner.py
COPY geometry_check_job.py /app/geometry_check_job.py

RUN python -c "import cadquery; import cad_ast_validator; import geometry; import geometry.execution"

CMD ["python", "cad_validation_worker.py"]
