"""Мягкие (soft) ограничения и целевая функция модели CP-SAT.

Модель реализуема всегда: любое «неудобство» штрафуется, а не делает задачу
неразрешимой. Здесь собираются штрафные переменные и итоговая цель.

Целевая функция (максимизация):
    + награда за каждую поставленную пару (лекции — с повышенным весом)
    - штраф за «окна» у групп/студентов
    - штраф за перегрузку преподавателей по парам в день

Недостающие обязательные пары (missing_pairs-переменные) в целевую НЕ входят:
placed_pairs == sum(x), поэтому штраф за них эквивалентен награде за placed.
"""

from __future__ import annotations

from ortools.sat.python import cp_model

from ..enums import LessonType
from .config import LECTURE_REWARD, PENALTY_TEACHER_OVERLOAD, PENALTY_WINDOW, REWARD_PLACED
from .dto import EventSpec


def collect_teacher_overload_penalties(model: cp_model.CpModel, specs: list[EventSpec],
                                       day_of_week_by_slot_id: dict[int, int],
                                       pair_placement_vars: dict,
                                       teachers_by_id: dict[int, dict]) -> list:
    """Штраф за превышение max_pairs_per_day преподавателя (мягко)."""
    vars_by_teacher_day: dict[tuple[int, int], list] = {}
    for spec in specs:
        if spec.teacher_id is None:
            continue
        for slot_id in day_of_week_by_slot_id:
            vars_by_teacher_day.setdefault(
                (spec.teacher_id, day_of_week_by_slot_id[slot_id]), []).append(
                pair_placement_vars[(spec.event_id, slot_id)]
            )

    overload_vars = []
    for (teacher_id, day_of_week), teacher_day_vars in vars_by_teacher_day.items():
        max_pairs_per_day = teachers_by_id.get(teacher_id, {}).get("max_pairs_per_day")
        if max_pairs_per_day and len(teacher_day_vars) > max_pairs_per_day:
            overload_var = model.NewIntVar(0, len(teacher_day_vars),
                                          f"overload_{teacher_id}_{day_of_week}")
            model.Add(overload_var >= sum(teacher_day_vars) - max_pairs_per_day)
            overload_vars.append(overload_var)
    return overload_vars


def collect_window_penalties(model: cp_model.CpModel, specs: list[EventSpec],
                             slot_ids: list[int],
                             day_of_week_by_slot_id: dict[int, int],
                             pair_placement_vars: dict) -> list:
    """Штраф за «окна»: пропущенный слот между двумя занятыми в течение дня.

    Считается для учебных групп (занятия на группу/поток) и отдельно для
    студентов (ПД блокирует конкретных студентов, а не их учебные группы).
    """
    window_vars = []
    days_of_week = sorted(set(day_of_week_by_slot_id.values()))
    # slot_ids упорядочены по (day, lesson_number) — см. generator.py
    slot_ids_by_day = {
        day: [slot_id for slot_id in slot_ids if day_of_week_by_slot_id[slot_id] == day]
        for day in days_of_week
    }

    placement_terms_by_attendee: dict[tuple[str, int], dict[int, list]] = {}
    for spec in specs:
        for slot_id in slot_ids:
            placement_var = pair_placement_vars[(spec.event_id, slot_id)]
            for group_id in spec.blocking_group_ids:
                placement_terms_by_attendee.setdefault(
                    ("group", group_id), {}).setdefault(slot_id, []).append(placement_var)
            for student_id in spec.blocking_student_ids:
                placement_terms_by_attendee.setdefault(
                    ("student", student_id), {}).setdefault(slot_id, []).append(placement_var)

    for (attendee_kind, attendee_id), terms_by_slot in placement_terms_by_attendee.items():
        for day in days_of_week:
            day_slot_ids = slot_ids_by_day[day]
            busy_vars = []
            for index, slot_id in enumerate(day_slot_ids):
                busy_var = model.NewBoolVar(
                    f"busy_{attendee_kind}{attendee_id}_{day}_{index}")
                slot_terms = terms_by_slot.get(slot_id, [])
                if slot_terms:
                    model.AddMaxEquality(busy_var, slot_terms)
                else:
                    model.Add(busy_var == 0)
                busy_vars.append(busy_var)
            # окно: busy[i]==1 и busy[j]==1 (j>i+1), всё между пусто
            for i in range(len(busy_vars)):
                for j in range(i + 2, len(busy_vars)):
                    gap_var = model.NewBoolVar(
                        f"gap_{attendee_kind}{attendee_id}_{day}_{i}_{j}")
                    middle_busy_vars = busy_vars[i + 1:j]
                    # gap <= busy_i, gap <= busy_j, gap <= 1 - mid_k (все k)
                    model.Add(gap_var <= busy_vars[i])
                    model.Add(gap_var <= busy_vars[j])
                    for middle_busy_var in middle_busy_vars:
                        model.Add(gap_var <= 1 - middle_busy_var)
                    window_vars.append(gap_var)
    return window_vars


def build_objective(model: cp_model.CpModel, specs: list[EventSpec],
                    slot_ids: list[int], pair_placement_vars: dict,
                    window_vars: list, teacher_overload_vars: list) -> None:
    """Собирает и устанавливает целевую функцию (см. докстринг модуля)."""
    reward_terms = []
    for spec in specs:
        pair_reward = (LECTURE_REWARD if spec.lesson_type == LessonType.LECTURE
                       else REWARD_PLACED)
        for slot_id in slot_ids:
            reward_terms.append(pair_reward * pair_placement_vars[(spec.event_id, slot_id)])

    model.Maximize(
        sum(reward_terms)
        - PENALTY_WINDOW * sum(window_vars)
        - PENALTY_TEACHER_OVERLOAD * sum(teacher_overload_vars)
    )
