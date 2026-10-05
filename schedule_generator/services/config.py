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


# --- Веса целевой функции (масштаб: одна поставленная пара = REWARD_PLACED) ---
REWARD_PLACED = 100                 # базовая награда за поставленную пару
LECTURE_REWARD = int(REWARD_PLACED * 1.5)  # лекции важнее: их слушает много групп
PENALTY_WINDOW = 3                  # штраф за «окно» у группы/студента в день
PENALTY_TEACHER_OVERLOAD = 5        # штраф за превышение max_pairs_per_day
