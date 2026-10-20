FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN pip install --no-cache-dir django-cors-headers django-environ django-extensions \
    djangorestframework drf-spectacular ortools psycopg2-binary gunicorn whitenoise
COPY . .
RUN chmod +x docker-entrypoint.sh
EXPOSE 8000
ENTRYPOINT ["./docker-entrypoint.sh"]
