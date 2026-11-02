"""Правила размещения занятий по дням недели.

Пары идут с понедельника по субботу. Понедельник и среда — дни назначения
генерируемой ПД, а не полный запрет остальных занятий:

- PD_ALLOWED_DAYS      — дни, занятые под ПД (пн=0, ср=2);
- REGULAR_ALLOWED_DAYS — учебные дни для остальных пар (пн–сб).

Внеучебка и ДПО могут идти в любой день недели (в т.ч. в пн/ср): они
планируются на отдельные контингенты (выборочная внеучебка, группы ДПО),
а пересечения закрывают ограничениями занятости групп/студентов.

Значения — правила предметной области по умолчанию; при изменении правил
править только это место (генератор читает allowed_days из EventSpec).
"""

from __future__ import annotations

from ..enums import LessonType

# 0=пн, 1=вт, 2=ср, 3=чт, 4=пт, 5=сб (см. TimeSlot.DAY_CHOICES)
PD_ALLOWED_DAYS = frozenset({0, 2})             # проектная деятельность: пн и ср
REGULAR_ALLOWED_DAYS = frozenset(range(6))      # обычные пары: пн–сб
ALL_WEEKDAYS = frozenset(range(6))              # пн–сб


def allowed_days_for(lesson_type: str) -> frozenset[int]:
    """Разрешённые дни размещения для типа занятия."""
    if lesson_type == LessonType.PD:
        return PD_ALLOWED_DAYS
    if lesson_type in (LessonType.EXTRACURRICULAR, LessonType.DPO):
        return ALL_WEEKDAYS  # внеучебка и ДПО — в любой день
    return REGULAR_ALLOWED_DAYS
