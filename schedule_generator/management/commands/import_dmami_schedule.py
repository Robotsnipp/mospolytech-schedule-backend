"""Load the published schedule export into the configured Django database."""

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from schedule_generator.services.dmami_import import import_dmami_schedule


class Command(BaseCommand):
    help = "Импортирует schedule.json rasp.dmami.ru в БД с сохранением периодов и исходных записей."

    def add_arguments(self, parser):
        parser.add_argument("document", type=Path)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        try:
            result = import_dmami_schedule(options["document"].read_bytes(), dry_run=options["dry_run"])
        except (ValueError, TypeError, OSError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(result, ensure_ascii=False))
