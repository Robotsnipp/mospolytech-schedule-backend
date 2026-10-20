"""Импорт расписания групп с rasp.dmami.ru в БД."""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from rasp.importer import MAX_CONCURRENCY, load_group_flags, run_import


class Command(BaseCommand):
    help = "Импортирует расписание групп с rasp.dmami.ru (кэш сырых ответов: data/raw/)."

    def add_arguments(self, parser):
        parser.add_argument("--groups", help="Номера групп через запятую (по умолчанию все).")
        parser.add_argument("--session", default="all", choices=["0", "1", "all"])
        parser.add_argument("--refresh", action="store_true", help="Игнорировать кэш.")
        parser.add_argument("--concurrency", type=int, default=3,
                            help=f"Параллельных запросов (не более {MAX_CONCURRENCY}).")

    def handle(self, *args, **options):
        try:
            flags = load_group_flags(settings.RASP_GROUPS_FILE)
        except (OSError, ValueError, KeyError) as exc:
            raise CommandError(f"Не удалось прочитать {settings.RASP_GROUPS_FILE}: {exc}") from exc
        if options["groups"]:
            wanted = [g.strip() for g in options["groups"].split(",") if g.strip()]
            unknown = [g for g in wanted if g not in flags]
            if unknown:
                raise CommandError(f"Групп нет в groups.json: {', '.join(unknown)}")
            flags = {g: flags[g] for g in wanted}
        sessions = [0, 1] if options["session"] == "all" else [int(options["session"])]
        if options["concurrency"] > MAX_CONCURRENCY:
            self.stderr.write(f"concurrency ограничен до {MAX_CONCURRENCY}")

        result = run_import(flags, sessions, settings.RASP_RAW_DIR, refresh=options["refresh"],
                            concurrency=options["concurrency"], log=self.stdout.write)
        self.stdout.write(
            f"\nИтого: успешно {result['ok']}, пусто {result['empty']}, "
            f"ошибок {result['errors']}; импортировано занятий {result['lessons_imported']}")
        self.stdout.write(f"В БД: {result['totals']}")
        if result["failed"]:
            self.stdout.write("Ошибки:")
            for title, session, reason in result["failed"]:
                self.stdout.write(f"  {title} (session={session}): {reason}")
