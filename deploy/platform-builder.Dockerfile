FROM python:3.11-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
COPY requirements-factory.txt requirements-factory-dev.txt /opt/factory/
RUN pip install --no-cache-dir -r /opt/factory/requirements-factory-dev.txt 'pandas>=2.2,<3'
ENV PYTHONDONTWRITEBYTECODE=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MPLCONFIGDIR=/tmp/matplotlib
USER 65534:65534
WORKDIR /work
ENTRYPOINT ["python"]
