"""Самые свободные периоды для набора групп (для планирования мероприятий).

Занятость берётся из реального расписания rasp.dmami.ru (rasp.Lesson): пара
занимает группу в день, если день недели совпадает и дата попадает в
date_from..date_to занятия (пустая граница - без ограничения).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from django.db.models import Q

from rasp.models import Group, Lesson
from rasp.pair_times import PAIR_TIMES

MAX_GROUPS = 30
MAX_DAYS = 62
MAX_LIMIT = 200


class FreePeriodsError(ValueError):
    """Неверные входные данные запроса."""


@dataclass
class Period:
    day: date
    pair_from: int
    pair_to: int
    busy: frozenset[str]
    total: int
    all_titles: tuple[str, ...]

    @property
    def free_count(self) -> int:
        return self.total - len(self.busy)

    @property
    def length(self) -> int:
        return self.pair_to - self.pair_from + 1

    def as_dict(self) -> dict:
        return {
            "date": self.day.isoformat(),
            "weekday": self.day.isoweekday(),
            "pair_from": self.pair_from,
            "pair_to": self.pair_to,
            "start": PAIR_TIMES[self.pair_from][0].strftime("%H:%M"),
            "end": PAIR_TIMES[self.pair_to][1].strftime("%H:%M"),
            "free_count": self.free_count,
            "total": self.total,
            "free_groups": [t for t in self.all_titles if t not in self.busy],
            "busy_groups": [t for t in self.all_titles if t in self.busy],
        }


def resolve_groups(titles: list[str]) -> list[Group]:
    """Группы по названиям в порядке запроса; неизвестные - ошибка."""
    titles = list(dict.fromkeys(t.strip() for t in titles if t.strip()))
    if not titles:
        raise FreePeriodsError("Укажите хотя бы одну группу: groups=231-333,231-334.")
    if len(titles) > MAX_GROUPS:
        raise FreePeriodsError(f"Не больше {MAX_GROUPS} групп за раз.")
    found = {g.title: g for g in Group.objects.filter(title__in=titles)}
    missing = [t for t in titles if t not in found]
    if missing:
        raise FreePeriodsError("Группы не найдены: " + ", ".join(missing))
    return [found[t] for t in titles]


def _days(date_from: date, date_to: date, include_sunday: bool):
    day = date_from
    while day <= date_to:
        if include_sunday or day.isoweekday() != 7:
            yield day
        day += timedelta(days=1)


def find_free_periods(groups: list[Group], date_from: date, date_to: date, *,
                      include_sunday: bool = False, min_free: int = 1,
                      limit: int = 20) -> list[Period]:
    """Периоды (подряд идущие пары одного дня с одинаковой занятостью),
    отсортированные: больше свободных групп, длиннее, раньше."""
    if date_to < date_from:
        raise FreePeriodsError("date_to раньше date_from.")
    if (date_to - date_from).days + 1 > MAX_DAYS:
        raise FreePeriodsError(f"Диапазон не длиннее {MAX_DAYS} дней.")
    if not 1 <= limit <= MAX_LIMIT:
        raise FreePeriodsError(f"limit должен быть от 1 до {MAX_LIMIT}.")

    titles = tuple(g.title for g in groups)
    total = len(groups)
    lessons = (
        Lesson.objects.filter(group__in=groups, pair__in=PAIR_TIMES.keys())
        .filter(Q(date_from__isnull=True) | Q(date_from__lte=date_to))
        .filter(Q(date_to__isnull=True) | Q(date_to__gte=date_from))
        .values_list("group__title", "weekday", "pair", "date_from", "date_to")
    )
    by_weekday: dict[int, list[tuple]] = {}
    for row in lessons:
        by_weekday.setdefault(row[1], []).append(row)

    periods: list[Period] = []
    for day in _days(date_from, date_to, include_sunday):
        busy_by_pair: dict[int, set[str]] = {pair: set() for pair in PAIR_TIMES}
        for title, _, pair, start, end in by_weekday.get(day.isoweekday(), []):
            if (start is None or start <= day) and (end is None or end >= day):
                busy_by_pair[pair].add(title)
        current: Period | None = None
        for pair in sorted(PAIR_TIMES):
            busy = frozenset(busy_by_pair[pair])
            if current and current.busy == busy and current.pair_to == pair - 1:
                current.pair_to = pair
                continue
            current = Period(day, pair, pair, busy, total, titles)
            periods.append(current)

    periods = [p for p in periods if p.free_count >= max(min_free, 1)]
    periods.sort(key=lambda p: (-p.free_count, -p.length, p.day, p.pair_from))
    return periods[:limit]
