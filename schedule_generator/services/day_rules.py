"""Правила размещения занятий по дням недели.

Университет: пары идут с понедельника по субботу; на проектную деятельность
(ПД) выделены понедельник и среда — в эти дни другие пары планироваться не
могут. Отсюда три множества разрешённых дней:

- PD_ALLOWED_DAYS      — дни, занятые под ПД (пн=0, ср=2);
- LECTURE_ALLOWED_DAYS — лекции: любой день кроме воскресенья (пн–сб);
- REGULAR_ALLOWED_DAYS — остальные очные пары (вт, чт, пт, сб).

Лекции могут ставиться в любой день (кроме вс), включая дни ПД, но с
пропуском между лекцией и соседними занятиями того же слушателя — это
жёсткое ограничение «не подряд» в constraints.add_lecture_not_adjacent
(соседний слот того же дня блокируется для всех групп потока).

Внеучебка и ДПО могут идти в любой день недели (в т.ч. в пн/ср): они
планируются на отдельные контингенты (выборочная внеучебка, группы ДПО),
а пересечения закрывают ограничениями занятости групп/студентов.

Значения — правила предметной области по умолчанию; при изменении правил
править только это место (генератор читает allowed_days из EventSpec).
"""

from __future__ import annotations

from ..enums import LessonType

# 0=пн, 1=вт, 2=ср, 3=чт, 4=пт, 5=сб, 6=вс (см. TimeSlot.DAY_CHOICES)
PD_ALLOWED_DAYS = frozenset({0, 2})             # проектная деятельность: пн и ср
LECTURE_ALLOWED_DAYS = frozenset(range(6))      # лекции: пн–сб (воскресенье — выходной)
REGULAR_ALLOWED_DAYS = frozenset({1, 3, 4, 5})  # обычные пары: вт, чт, пт, сб
ALL_WEEKDAYS = frozenset(range(6))              # пн–сб


def allowed_days_for(lesson_type: str) -> frozenset[int]:
    """Разрешённые дни размещения для типа занятия."""
    if lesson_type == LessonType.PD:
        return PD_ALLOWED_DAYS
    if lesson_type == LessonType.LECTURE:
        return LECTURE_ALLOWED_DAYS  # лекции — любой день, кроме воскресенья
    if lesson_type in (LessonType.EXTRACURRICULAR, LessonType.DPO):
        return ALL_WEEKDAYS  # внеучебка и ДПО — в любой день
    return REGULAR_ALLOWED_DAYS
