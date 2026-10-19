set windows-shell := ["powershell.exe", "-NoLogo", "-Command"]

run:
    uv run python manage.py runserver

migrate:
    uv run python manage.py migrate

resetdb:
    uv run python manage.py reset_db

generate:
    uv run python manage.py generate_demo_schedule --time-limit 60