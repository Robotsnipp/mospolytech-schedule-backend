set windows-shell := ["powershell.exe", "-NoLogo", "-Command"]

run:
    uv run python manage.py runserver

migrate:
    uv run python manage.py migrate

resetdb:
    uv run python manage.py reset_db

import-schedule:
    uv run python manage.py import_rasp

# Полный демо-пример (повторно - пересоздаёт)
demo:
    uv run python manage.py seed_demo --reset

# Тесты всех приложений на отдельной SQLite-БД
test:
    uv run python manage.py test schedule_generator rasp events

# Линтер (та же версия, что в CI)
lint:
    uvx ruff==0.17.0 check .

# Проверка, что нет незаписанных изменений моделей
check-migrations:
    uv run python manage.py makemigrations --check --dry-run
