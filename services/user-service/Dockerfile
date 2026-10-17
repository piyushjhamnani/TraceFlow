# User Service image
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8001

WORKDIR /app

# Dependencies first: this layer is cached and only rebuilt when requirements change
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py telemetry.py ./

# Do not run as root inside the container
RUN useradd --create-home appuser
USER appuser

EXPOSE 8001
CMD ["sh", "-c", "exec uvicorn app:app --host 0.0.0.0 --port ${PORT}"]