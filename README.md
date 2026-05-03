# SylvaLens - Raster Service

A high-performance **FastAPI** microservice dedicated to raster and point-cloud analytics for the SylvaLens platform.

## Features
- **FORMS-T Analysis:** Calculates AGBD, Height, and WVD using `rasterstats`.
- **Hansen GFC Analysis:** Calculates forest cover and year-over-year loss.
- **LiDAR HD Processing:** Uses `PDAL` to dynamically crop and calculate height percentiles from COPC LAZ point clouds.
- **OpenAPI:** Automatically generates API contracts for frontend consumption.

## Development Setup

### Prerequisites
- Python 3.10+
- The `forest-res` data volume must be properly configured (see the `infra` repository validation scripts).

### 1. Configuration
Copy the `.env.example` file to `.env`:
```bash
cp .env.example .env
```
Ensure the data paths (`FORMS_T_PATH`, etc.) point to your local datasets.

### 2. Local Setup (Native)
We recommend running this via Docker using the `infra` stack because it requires complex C++ dependencies like `PDAL` and `GDAL`.

If you wish to run it natively on Ubuntu/Linux:
```bash
apt-get install python3-dev libpdal-dev pdal
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```
Swagger documentation is available at `http://localhost:8000/docs`.

## Production Build
See the `sylvalens/infra` repository for production deployment orchestration using the optimized multi-stage Dockerfile.