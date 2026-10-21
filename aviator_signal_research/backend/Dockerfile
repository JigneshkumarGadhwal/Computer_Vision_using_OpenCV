FROM python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 git && rm -rf /var/lib/apt/lists/*
WORKDIR /srv/backend
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ /srv/backend/
COPY docs/ /srv/docs/
COPY README.md /srv/README.md
ENV AVR_DATABASE_URL=sqlite:////srv/data/aviator_research.db \
    AVR_RAW_DATA_DIR=/srv/data/raw \
    AVR_MODEL_DIR=/srv/data/models \
    AVR_REPORT_DIR=/srv/reports
EXPOSE 8000
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
