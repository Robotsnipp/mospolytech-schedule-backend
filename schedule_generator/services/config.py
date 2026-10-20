"""Параметры генерации: значения по умолчанию и веса целевой функции.

Все «магические числа» алгоритма собраны здесь — чтобы тюнинг не требовал
правки логики и был виден в одном месте.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SolverConfig:
    """Настройки решателя CP-SAT (можно переопределить на входе сервиса)."""

    time_limit_seconds: int = 30
    seed: int = 17
    num_search_workers: int = 8


# Полнота размещения всегда важнее мягких штрафов; масштаб задаёт objective.
WINDOW_FREE_MINUTES = 30           # обычный перерыв не считается окном
PENALTY_WINDOW = 1                 # за минуту ожидания сверх перерыва на человека
PENALTY_TEACHER_OVERLOAD = 60       # за пару сверх дневного лимита преподавателя
