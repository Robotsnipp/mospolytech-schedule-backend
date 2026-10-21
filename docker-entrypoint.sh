#!/bin/sh
set -e
python manage.py collectstatic --noinput -v0
python manage.py migrate --noinput
python manage.py load_pe
python manage.py import_pd
# Первый старт: расписание подтягивается в фоне (кэш в RASP_RAW_DIR сохраняется между запусками).
if [ "$(python manage.py shell -c 'from rasp.models import Lesson; print(Lesson.objects.count())' | tail -n 1)" = "0" ]; then
  python manage.py import_rasp > /tmp/import_rasp.log 2>&1 &
fi
exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 2 --timeout 120
