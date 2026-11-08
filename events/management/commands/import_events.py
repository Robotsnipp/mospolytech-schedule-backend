import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from events.importer import EventDataError, import_events


class Command(BaseCommand):
    help = "Импортирует мероприятия из JSON-файла (повторный импорт обновляет записи)."

    def add_arguments(self, parser):
        parser.add_argument("path", help="Путь к JSON-файлу, например data/events/events_2025.json")

    def handle(self, *args, **options):
        path = Path(options["path"])
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise CommandError(f"Файл не найден: {path}") from exc
        except json.JSONDecodeError as exc:
            raise CommandError(f"Файл не является корректным JSON: {exc}") from exc
        try:
            result = import_events(data)
        except EventDataError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(
            f"Создано: {result['created']}, обновлено: {result['updated']}, "
            f"пропущено: {result['skipped']}"
        ))
