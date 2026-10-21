"""Импорт проектной деятельности из опубликованной Google-таблицы.

Сырой CSV кэшируется в data/pd/pd_schedule.csv; повторный запуск берёт его из файла,
``--refresh`` скачивает заново.
"""

import urllib.error
import urllib.request

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from rasp import pd_service
from rasp.pd_parser import PDParseError

CSV_URL = ("https://docs.google.com/spreadsheets/d/e/"
           "2PACX-1vRHgwt89VM13uuUy8dDvpjlb-BDM4ovsIKhoLDGIaI58NJtAawxABVW7sdVRDDH5UGaM2bsryni6Im7/"
           "pub?gid=643150359&single=true&output=csv")


class Command(BaseCommand):
    help = "Загружает проекты и их расписание (data/pd/pd_schedule.csv или из сети с --refresh)."

    def add_arguments(self, parser):
        parser.add_argument("--refresh", action="store_true", help="Скачать таблицу заново.")

    def handle(self, *args, **options):
        path = settings.BASE_DIR / "data" / "pd" / "pd_schedule.csv"
        if options["refresh"] or not path.exists():
            try:
                with urllib.request.urlopen(CSV_URL, timeout=30) as response:
                    body = response.read().decode("utf-8")
            except (urllib.error.URLError, TimeoutError, UnicodeDecodeError) as exc:
                raise CommandError(f"Не удалось скачать таблицу: {exc}") from exc
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
        try:
            result = pd_service.load_file(path)
        except (PDParseError, OSError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(
            f"Проектная деятельность: проектов {result['projects']}, слотов {result['slots']}, "
            f"направлений {result['directions']}, удалено старых {result['removed']}."))
