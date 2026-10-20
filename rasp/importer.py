"""Импорт расписаний групп в БД: сеть — в потоках, запись — в основном потоке."""

from __future__ import annotations

import json
import logging
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from django.db import transaction

from .client import FetchError, fetch_group
from .models import Group, Lesson, Room, Subject, Teacher
from .parser import ParsedSchedule, ScheduleUnavailable, parse_response

logger = logging.getLogger(__name__)
MAX_CONCURRENCY = 3


def load_group_flags(path: Path) -> dict[str, bool]:
    return json.loads(path.read_text(encoding="utf-8"))["groups"]


@transaction.atomic
def store_group(title: str, flag: bool, schedule: ParsedSchedule | None, session: int) -> int:
    """Заменяет занятия группы выбранной сессии одной транзакцией. Возвращает число занятий.

    ``schedule=None`` — у группы нет расписания (нормально для сессии): старые занятия
    этой части удаляются, сама группа сохраняется.
    """
    info = schedule.group if schedule else {}
    group, _ = Group.objects.update_or_create(title=title, defaults={
        "flag": flag,
        **({"course": info.get("course"), "date_from": info.get("dateFrom"),
            "date_to": info.get("dateTo"), "evening": bool(info.get("evening")),
            "comment": info.get("comment") or ""} if info else {}),
    })
    Lesson.objects.filter(group=group, is_session=bool(session)).delete()
    if schedule is None:
        return 0

    subjects: dict[str, Subject] = {}
    teachers: dict[int, Teacher] = {}
    rooms: dict[tuple, Room] = {}
    teacher_links: list[list[int]] = []
    room_links: list[list[tuple]] = []
    lessons: list[Lesson] = []

    for item in schedule.lessons:
        subject = subjects.get(item.subject)
        if subject is None:
            subject, _ = Subject.objects.get_or_create(name=item.subject)
            subjects[item.subject] = subject
        for t in item.teachers:
            if t.id not in teachers:
                teachers[t.id], _ = Teacher.objects.update_or_create(
                    id=t.id, defaults={"name": t.name, "is_vacancy": t.is_vacancy})
        room_keys = []
        for r in item.rooms:
            key = (r.name, item.location, r.url)
            if key not in rooms:
                rooms[key], _ = Room.objects.get_or_create(
                    name=r.name, location=item.location, url=r.url,
                    defaults={"raw_title": r.raw_title})
            room_keys.append(key)
        lessons.append(Lesson(
            group=group, subject=subject, weekday=item.weekday, pair=item.pair,
            type=item.type, date_from=item.date_from, date_to=item.date_to, dts=item.dts,
            is_session=bool(session), week=item.week, e_link=item.e_link,
            location=item.location))
        teacher_links.append([t.id for t in item.teachers])
        room_links.append(room_keys)

    Lesson.objects.bulk_create(lessons)
    # bulk_create без RETURNING id не гарантирован на всех СУБД — перечитываем по порядку.
    saved = list(Lesson.objects.filter(group=group, is_session=bool(session)).order_by("id"))
    through_t = Lesson.teachers.through
    through_r = Lesson.rooms.through
    through_t.objects.bulk_create([
        through_t(lesson_id=lesson.id, teacher_id=tid)
        for lesson, tids in zip(saved, teacher_links, strict=True) for tid in tids])
    through_r.objects.bulk_create([
        through_r(lesson_id=lesson.id, room_id=rooms[key].id)
        for lesson, keys in zip(saved, room_links, strict=True) for key in keys])
    return len(lessons)


def _fetch(title: str, session: int, raw_dir: Path, refresh: bool):
    return fetch_group(title, session, raw_dir, refresh=refresh)


def run_import(groups: dict[str, bool], sessions: list[int], raw_dir: Path, refresh: bool = False,
               concurrency: int = 3, log: Callable[[str], None] = print) -> dict:
    """Импортирует группы; ошибка одной группы не прерывает остальных."""
    concurrency = max(1, min(concurrency, MAX_CONCURRENCY))
    tasks = [(title, session) for title in groups for session in sessions]
    stats: Counter = Counter()
    failed: list[tuple[str, int, str]] = []
    done = 0

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(_fetch, t, s, raw_dir, refresh): (t, s) for t, s in tasks}
        for future in as_completed(futures):
            title, session = futures[future]
            done += 1
            prefix = f"[{done}/{len(tasks)}] {title} s{session}"
            try:
                payload, cached = future.result()
                try:
                    schedule = parse_response(payload)
                except ScheduleUnavailable as exc:
                    if not exc.empty:
                        raise
                    store_group(title, groups[title], None, session)
                    stats["empty"] += 1
                    log(f"{prefix}: пусто ({exc.reason})")
                    continue
                count = store_group(title, groups[title], schedule, session)
                stats["ok" if count else "empty"] += 1
                stats["lessons"] += count
                log(f"{prefix}: {count} занятий{' (кэш)' if cached else ''}")
            except (FetchError, ScheduleUnavailable) as exc:
                stats["errors"] += 1
                failed.append((title, session, str(exc)))
                logger.warning("%s: ошибка: %s", prefix, exc)
                log(f"{prefix}: ОШИБКА {exc}")
            except Exception as exc:  # noqa: BLE001 — одна группа не должна ронять импорт
                stats["errors"] += 1
                failed.append((title, session, f"{type(exc).__name__}: {exc}"))
                logger.exception("%s: неожиданная ошибка", prefix)
                log(f"{prefix}: ОШИБКА {type(exc).__name__}: {exc}")

    return {
        "ok": stats["ok"], "empty": stats["empty"], "errors": stats["errors"],
        "lessons_imported": stats["lessons"], "failed": failed,
        "totals": {"groups": Group.objects.count(), "lessons": Lesson.objects.count(),
                   "teachers": Teacher.objects.count(), "rooms": Room.objects.count(),
                   "subjects": Subject.objects.count()},
    }
