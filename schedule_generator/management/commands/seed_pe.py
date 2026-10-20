"""Заполняет направления и слоты физкультуры (идемпотентно)."""

from datetime import time

from django.core.management.base import BaseCommand

from schedule_generator.models import PEDirection, PESlot

DIRECTIONS = [
    ("aerobics", "Аэробика", "🤸", "Спортзал, зал 2"),
    ("volleyball", "Волейбол", "🏐", "Спортзал, зал 1"),
    ("basketball", "Баскетбол", "🏀", "Спортзал, зал 1"),
    ("football", "Футбол", "⚽", "Стадион, поле 1"),
    ("swimming", "Плавание", "🏊", "Бассейн, дорожка 3"),
    ("athletics", "Лёгкая атлетика", "🏃", "Стадион, дорожка"),
    ("chess", "Шахматы", "♟️", "Корпус Б, Б-110"),
    ("tabletennis", "Настольный теннис", "🏓", "Спортзал, зал 3"),
    ("yoga", "Йога", "🧘", "Спортзал, зал 2"),
]
TIMES = [(time(9, 0), time(10, 30)), (time(10, 40), time(12, 10)),
         (time(12, 20), time(13, 50)), (time(14, 30), time(16, 0)),
         (time(16, 10), time(17, 40))]


class Command(BaseCommand):
    help = "Создаёт направления и слоты физкультуры."

    def handle(self, *args, **options):
        for code, name, icon, place in DIRECTIONS:
            direction, _ = PEDirection.objects.get_or_create(
                code=code, defaults={"name": name, "icon": icon})
            for day in range(6):
                for start, end in TIMES:
                    PESlot.objects.get_or_create(
                        direction=direction, day_of_week=day, start_time=start,
                        defaults={"end_time": end, "place": place})
        self.stdout.write(self.style.SUCCESS("Слоты физкультуры созданы."))
