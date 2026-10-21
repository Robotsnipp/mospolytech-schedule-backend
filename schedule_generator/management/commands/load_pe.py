"""Загружает реальное расписание физкультуры (data/pe/pe_schedule.json) в БД.

JSON получен из PDF командой scripts/parse_pe_pdf.py. Загрузка идемпотентна и заменяет
набор слотов: слоты, которых нет в файле, удаляются вместе с записями на них.
"""

import json
from datetime import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from schedule_generator.models import PEDirection, PESlot

# Название ячейки в PDF -> направления (код, название, иконка). Одна ячейка может дать несколько.
DIRECTIONS = {
    "баскетбол": [("basketball", "Баскетбол", "🏀")],
    "волейбол": [("volleyball", "Волейбол", "🏐")],
    "пауэрлифтинг, кроссфит": [("powerlifting", "Пауэрлифтинг", "🏋️"), ("crossfit", "Кроссфит", "💪")],
    "ОФП": [("ofp", "ОФП", "🏃")],
    "ОФП (тренажерный зал)": [("ofp-gym", "ОФП (тренажёрный зал)", "🏋️")],
    "ОФП / футзал": [("ofp-futsal", "ОФП / футзал", "⚽")],
    "СМГ настольный теннис": [("smg-table-tennis", "СМГ: настольный теннис", "🏓")],
    "СМГ": [("smg", "СМГ", "🧘")],
    "аэробика": [("aerobics", "Аэробика", "🤸")],
}


def parse_time(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def venue_place(venue: dict) -> str:
    return ", ".join(part for part in (venue["name"], venue["metro"], venue["address"]) if part)


class Command(BaseCommand):
    help = "Загружает направления и слоты физкультуры из data/pe/pe_schedule.json."

    def handle(self, *args, **options):
        path = settings.BASE_DIR / "data" / "pe" / "pe_schedule.json"
        try:
            cells = json.loads(path.read_text(encoding="utf-8"))["slots"]
        except (OSError, ValueError, KeyError) as exc:
            raise CommandError(f"Не удалось прочитать {path}: {exc}") from exc
        unknown = {c["label"] for c in cells} - DIRECTIONS.keys()
        if unknown:
            raise CommandError(f"Неизвестные направления в файле: {sorted(unknown)}")

        with transaction.atomic():
            directions = {}
            for entries in DIRECTIONS.values():
                for code, name, icon in entries:
                    directions[code], _ = PEDirection.objects.update_or_create(
                        code=code, defaults={"name": name, "icon": icon})
            keep: set[int] = set()
            for cell in cells:
                for code, _, _ in DIRECTIONS[cell["label"]]:
                    for venue in cell["venues"]:
                        slot, _ = PESlot.objects.update_or_create(
                            direction=directions[code], day_of_week=cell["day"],
                            start_time=parse_time(cell["start"]), place=venue_place(venue),
                            defaults={"end_time": parse_time(cell["end"])})
                        keep.add(slot.id)
            removed = PESlot.objects.exclude(id__in=keep).delete()[0]
            PEDirection.objects.exclude(code__in=directions).delete()
        self.stdout.write(self.style.SUCCESS(
            f"Физкультура: направлений {len(directions)}, слотов {len(keep)}, удалено старых {removed}."))
