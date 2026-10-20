# Standalone (non-Home-Assistant-add-on) image: docker compose up -d, with the variables from .env
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app
WORKDIR /app
COPY spa2mqtt_addon/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY spa2mqtt_addon/spa2mqtt /app/spa2mqtt
CMD ["python", "-m", "spa2mqtt.main"]
