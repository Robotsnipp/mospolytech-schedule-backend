"""Индекс усталости и «окна»: чистые функции без ORM и без решателя.

Формула усталости одна на всё приложение: те же константы (``config.py``)
использует решатель в ``objectives.collect_fatigue_penalties`` и отчёт запуска.
Поэтому число в отчёте соответствует тому, что минимизировал решатель.

В индекс входят число пар, ранний старт и поздние пары. Окна и переезды
считаются отдельными метриками.
"""

from __future__ import annotations

from collections.abc import Iterable

from .config import (
    EARLY_LESSON_NUMBER,
    FATIGUE_COMFORT_LEVEL,
    FATIGUE_EARLY_WEIGHT,
    FATIGUE_LATE_WEIGHT,
    FATIGUE_PAIR_WEIGHT,
    LATE_LESSON_NUMBER,
)


def day_fatigue(lesson_numbers: Iterable[int]) -> int:
    """Усталость одного дня по номерам занятых пар (каждый номер — одна пара)."""
    numbers = list(lesson_numbers)
    if not numbers:
        return 0
    early = any(number <= EARLY_LESSON_NUMBER for number in numbers)
    late_count = sum(1 for number in numbers if number >= LATE_LESSON_NUMBER)
    return (FATIGUE_PAIR_WEIGHT * len(numbers)
            + FATIGUE_EARLY_WEIGHT * int(early)
            + FATIGUE_LATE_WEIGHT * late_count)


def max_day_fatigue(slot_lesson_numbers: Iterable[int]) -> int:
    """Верхняя граница усталости дня, если заняты все перечисленные слоты."""
    return day_fatigue(slot_lesson_numbers)


def count_windows(lesson_numbers: Iterable[int]) -> int:
    """Число свободных слотов между первой и последней парой дня."""
    numbers = sorted(set(lesson_numbers))
    if len(numbers) < 2:
        return 0
    return (numbers[-1] - numbers[0] + 1) - len(numbers)


def is_overloaded(fatigue: int) -> bool:
    """Выше ли усталость комфортного уровня."""
    return fatigue > FATIGUE_COMFORT_LEVEL
