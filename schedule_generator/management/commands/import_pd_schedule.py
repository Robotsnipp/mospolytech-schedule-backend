"""Импорт фиксированной ПД из проверенной выгрузки прототипа."""

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from schedule_generator.models import Module, Semester
from schedule_generator.services.pd_import import PDImportError, import_fixed_pd


class Command(BaseCommand):
    help = "Импортирует выбранные занятия ПД по явному файлу сопоставлений проектов, преподавателей и комнат."

    def add_arguments(self, parser):
        parser.add_argument("document", type=Path)
        parser.add_argument("--mapping", required=True, type=Path)
        parser.add_argument("--semester", required=True, type=int)
        parser.add_argument("--module", type=int)
        parser.add_argument("--travel-buffer-minutes", required=True, type=int)

    def handle(self, *args, **opts):
        try:
            result = import_fixed_pd(
                document=json.loads(Path(opts["document"]).read_text()),
                mapping=json.loads(Path(opts["mapping"]).read_text()),
                semester=Semester.objects.get(pk=opts["semester"]),
                module=Module.objects.get(pk=opts["module"]) if opts["module"] else None,
                travel_buffer_minutes=opts["travel_buffer_minutes"],
            )
        except (PDImportError, ValueError, KeyError, TypeError, OSError,
                Semester.DoesNotExist, Module.DoesNotExist) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(json.dumps(result, ensure_ascii=False)))
