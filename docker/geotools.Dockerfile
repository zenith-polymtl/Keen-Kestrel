# Image légère pour `make world` : génère les mondes Gazebo géoréférencés (worlds/build_world.py).
# Séparée de l'image sim pour ne pas la reconstruire (≈20-40 min) pour quelques paquets Python.
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends libexpat1 && \
    rm -rf /var/lib/apt/lists/* && \
    pip install --no-cache-dir rasterio==1.5.* pyproj numpy scipy pillow
ENV HOME=/tmp PYTHONUNBUFFERED=1
WORKDIR /worlds
