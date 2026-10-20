#!/bin/sh
set -e
python manage.py migrate --noinput
python manage.py seed_pe
if [ "$(python manage.py shell -c 'from schedule_generator.models import ScheduledClass as S; print(S.objects.count())' | tail -n 1)" = "0" ]; then
  python manage.py generate_demo_schedule --time-limit "${GEN_TIME:-20}"
fi
exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 2 --timeout 120
