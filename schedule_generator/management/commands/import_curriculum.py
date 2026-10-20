"""Импорт выбранных проверенных строк OCR-выгрузки учебного плана."""

import json
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from schedule_generator.services.curriculum import (
    CurriculumDataError,
    import_curriculum,
)


class Command(BaseCommand):
    help = "Импортирует учебный план из JSON с явным файлом проверки строк."

    def add_arguments(self, parser):
        parser.add_argument("document", type=Path)
        parser.add_argument("--review", required=True, type=Path)

    def handle(self, *args, **options):
        try:
            result = import_curriculum(
                document=json.loads(Path(options["document"]).read_text()),
                review=json.loads(Path(options["review"]).read_text()),
            )
        except (
            CurriculumDataError,
            ValidationError,
            ValueError,
            TypeError,
            KeyError,
            OSError,
        ) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(result, ensure_ascii=False))
