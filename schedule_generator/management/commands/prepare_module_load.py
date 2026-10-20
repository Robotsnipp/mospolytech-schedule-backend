"""Явное распределение академических часов группы в выбранном модуле."""

import json
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from schedule_generator.models import Group, Module
from schedule_generator.services.curriculum import (
    CurriculumDataError,
    prepare_module_load,
)


class Command(BaseCommand):
    help = "Создаёт/обновляет LessonEvent по проверенному распределению часов модуля."

    def add_arguments(self, parser):
        parser.add_argument("allocation", type=Path)
        parser.add_argument("--group", required=True, type=int)
        parser.add_argument("--module", required=True, type=int)

    def handle(self, *args, **options):
        try:
            allocation = json.loads(Path(options["allocation"]).read_text())
            result = prepare_module_load(
                group=Group.objects.get(pk=options["group"]),
                module=Module.objects.get(pk=options["module"]),
                **allocation,
            )
        except (
            CurriculumDataError,
            ValidationError,
            ValueError,
            TypeError,
            KeyError,
            OSError,
            Group.DoesNotExist,
            Module.DoesNotExist,
        ) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(result, ensure_ascii=False))
