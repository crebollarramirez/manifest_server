FROM condaforge/miniforge3:latest

WORKDIR /app

# Install system basics
RUN apt-get update && apt-get install -y \
    blender \
    git \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Create conda environment
COPY environment.yml /app/environment.yml
RUN mamba env create -f /app/environment.yml && mamba clean -afy

# Run commands from the geom environment without wrapping every invocation in `conda run`.
ENV PATH=/opt/conda/envs/geom/bin:$PATH

RUN blender --background --factory-startup --python-expr \
    "import bpy; assert bpy.app.background" \
    && python -c \
    "import cadquery, manifold3d, pymeshlab, trimesh"

# Copy worker code
COPY . /app

ENV PYTHONUNBUFFERED=1

CMD ["python", "cad_worker.py"]
