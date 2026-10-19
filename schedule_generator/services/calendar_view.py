"""Сервис развёртки недельного шаблона в календарные даты.

Решатель работает с недельным шаблоном (TimeSlot без дат). Для фронтенда
шаблон нужно привязать к конкретным неделям модуля: здесь — вычисление
учебных недель по датам модуля, чётности и выбор подходящего WeekPattern.

Договорённости (см. также .agents/planning.md):
- учебная неделя N модуля начинается в ближайший понедельник к start_date
  (если старт попал на середину недели, неполная первая неделя считается
  неделей 1 со своим понедельником до начала модуля не уходит: неделя
  начинается max(понедельник start_date, start_date));
- чётность недели считается от календарной ISO-недели: нечётные ISO-недели
  получают шаблон parity=1, чётные — parity=2; если у семестра только один
  паттерн с parity=0 («Любая») — используется он для всех недель;
- суббота включается как день недели (индекс 5), воскресенье не планируется.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from ..models import Module, Semester, WeekPattern


@dataclass(frozen=True)
class CalendarWeek:
    """Одна учебная неделя модуля с календарными границами."""

    number: int          # 1..teaching_weeks
    monday: dt.date      # дата понедельника
    start_date: dt.date  # первый рабочий день недели (>= monday)
    end_date: dt.date    # последний день недели внутри модуля (<= sunday)
    parity: int          # 1 — нечётная ISO-неделя, 2 — чётная

    @property
    def is_parity_odd(self) -> bool:
        return self.parity == 1


def build_calendar_weeks(module: Module) -> list[CalendarWeek]:
    """Разворачивает модуль на список учебных недель с датами и чётностью."""
    weeks: list[CalendarWeek] = []
    first_monday = module.start_date - dt.timedelta(days=module.start_date.weekday())
    for n in range(1, module.teaching_weeks + 1):
        monday = first_monday + dt.timedelta(weeks=n - 1)
        sunday = monday + dt.timedelta(days=6)
        start = max(monday, module.start_date)
        end = min(sunday, module.end_date)
        if end < start:
            continue  # неделя целиком вне дат модуля
        iso_parity = monday.isocalendar().week % 2  # 1 -> нечётная
        weeks.append(CalendarWeek(
            number=n,
            monday=monday,
            start_date=start,
            end_date=end,
            parity=1 if iso_parity else 2,
        ))
    return weeks


def resolve_pattern_for_date(semester: Semester, week: CalendarWeek) -> WeekPattern | None:
    """Шаблон недели, действующий в эту календарную неделю семестра.

    Приоритет: точное совпадение по чётности -> «Любая» (parity=0) ->
    единственный паттерн -> первый по id. Возвращает None, если у семестра
    нет шаблонов вовсе.
    """
    patterns = list(WeekPattern.objects.filter(semester=semester).order_by("id"))
    if not patterns:
        return None
    exact = [p for p in patterns if p.parity == week.parity]
    if exact:
        return exact[0]
    any_parity = [p for p in patterns if p.parity == 0]
    if any_parity:
        return any_parity[0]
    return patterns[0]


def find_week_for_date(module: Module, target: dt.date) -> CalendarWeek | None:
    """Учебная неделя модуля, содержащая дату (или None)."""
    for week in build_calendar_weeks(module):
        if week.monday <= target <= week.monday + dt.timedelta(days=6):
            return week
    return None
