"""Мягкие (soft) ограничения и целевая функция модели CP-SAT.

Модель реализуема всегда: любое «неудобство» штрафуется, а не делает задачу
неразрешимой. Здесь собираются штрафные переменные и итоговая цель.

Целевая функция (максимизация):
    + награда за каждую поставленную пару (лекции — с повышенным весом)
    + бонус за сохранение пары при перепланировании (пины)
    - штраф за «окна» у групп/студентов
    - штраф за перегрузку преподавателей по парам в день
    - штраф за усталость групп выше комфортной и за самый тяжёлый день
    - штраф за пары не в хронотип преподавателя
    - штраф за дни сверх допустимых в режиме подготовки к сессии

Недостающие обязательные пары (missing_pairs-переменные) в целевую НЕ входят:
placed_pairs == sum(x), поэтому штраф за них эквивалентен награде за placed.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence

from ortools.sat.python import cp_model

from ..enums import Chronotype, LessonType
from .config import (
    CHRONOTYPE_EARLY_BAD_FROM,
    CHRONOTYPE_LATE_BAD_UNTIL,
    EARLY_LESSON_NUMBER,
    FATIGUE_COMFORT_LEVEL,
    FATIGUE_EARLY_WEIGHT,
    FATIGUE_LATE_WEIGHT,
    FATIGUE_PAIR_WEIGHT,
    LATE_LESSON_NUMBER,
    LECTURE_REWARD,
    PENALTY_TEACHER_OVERLOAD,
    PENALTY_WINDOW,
    REWARD_PLACED,
)
from .dto import EventSpec
from .wellbeing import max_day_fatigue

# Слагаемое целевой функции: (вес, переменные); итог вклада = вес * сумма переменных.
WeightedTerm = tuple[int, Sequence]


def _placement_terms_by_attendee(specs: list[EventSpec], slot_ids: list[int],
                                 pair_placement_vars: dict) -> dict[tuple[str, int], dict[int, list]]:
    """Для каждого слушателя («group» | «student», id) — переменные размещения
    по слотам: какие пары его блокируют в каждом слоте."""
    terms: dict[tuple[str, int], dict[int, list]] = {}
    for spec in specs:
        for slot_id in slot_ids:
            placement_var = pair_placement_vars[(spec.event_id, slot_id)]
            for group_id in spec.blocking_group_ids:
                terms.setdefault(("group", group_id), {}).setdefault(
                    slot_id, []).append(placement_var)
            for student_id in spec.blocking_student_ids:
                terms.setdefault(("student", student_id), {}).setdefault(
                    slot_id, []).append(placement_var)
    return terms


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

    placement_terms_by_attendee = _placement_terms_by_attendee(
        specs, slot_ids, pair_placement_vars)

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


def collect_fatigue_penalties(model: cp_model.CpModel, specs: list[EventSpec],
                              slot_ids: list[int],
                              day_of_week_by_slot_id: dict[int, int],
                              lesson_number_by_slot_id: dict[int, int],
                              pair_placement_vars: dict) -> tuple[list, list]:
    """Усталость групп и студентов по дням (формула — services/wellbeing.py).

    Возвращает (excess_vars, peak_vars):
    - excess_vars: для каждого дня каждого слушателя — превышение комфортного
      уровня усталости;
    - peak_vars: одна переменная — усталость самого тяжёлого дня среди всех
      (минимакс: расписание с одним ужасным днём хуже ровного).
    """
    excess_vars: list = []
    day_fatigue_exprs: list[tuple[object, int]] = []   # (выражение, верхняя граница)
    slot_ids_by_day: dict[int, list[int]] = defaultdict(list)
    for slot_id in slot_ids:
        slot_ids_by_day[day_of_week_by_slot_id[slot_id]].append(slot_id)

    terms_by_attendee = _placement_terms_by_attendee(specs, slot_ids, pair_placement_vars)
    for (attendee_kind, attendee_id), terms_by_slot in terms_by_attendee.items():
        for day, day_slot_ids in slot_ids_by_day.items():
            busy_slot_ids = [slot_id for slot_id in day_slot_ids if slot_id in terms_by_slot]
            if not busy_slot_ids:
                continue
            pair_terms = []
            early_terms = []
            for slot_id in busy_slot_ids:
                lesson_number = lesson_number_by_slot_id[slot_id]
                coefficient = FATIGUE_PAIR_WEIGHT + (
                    FATIGUE_LATE_WEIGHT if lesson_number >= LATE_LESSON_NUMBER else 0)
                pair_terms.extend(coefficient * var for var in terms_by_slot[slot_id])
                if lesson_number <= EARLY_LESSON_NUMBER:
                    early_terms.extend(terms_by_slot[slot_id])
            fatigue_expr = sum(pair_terms)
            if early_terms:
                early_var = model.NewBoolVar(f"early_{attendee_kind}{attendee_id}_{day}")
                model.AddMaxEquality(early_var, early_terms)
                fatigue_expr = fatigue_expr + FATIGUE_EARLY_WEIGHT * early_var
            upper_bound = max_day_fatigue(
                lesson_number_by_slot_id[slot_id] for slot_id in busy_slot_ids)
            day_fatigue_exprs.append((fatigue_expr, upper_bound))
            if upper_bound > FATIGUE_COMFORT_LEVEL:
                excess_var = model.NewIntVar(
                    0, upper_bound - FATIGUE_COMFORT_LEVEL,
                    f"fatigue_excess_{attendee_kind}{attendee_id}_{day}")
                model.Add(excess_var >= fatigue_expr - FATIGUE_COMFORT_LEVEL)
                excess_vars.append(excess_var)

    peak_vars: list = []
    if day_fatigue_exprs:
        peak_var = model.NewIntVar(0, max(bound for _, bound in day_fatigue_exprs),
                                   "fatigue_peak")
        for fatigue_expr, _ in day_fatigue_exprs:
            model.Add(peak_var >= fatigue_expr)
        peak_vars.append(peak_var)
    return excess_vars, peak_vars


def collect_chronotype_penalties(specs: list[EventSpec], slot_ids: list[int],
                                 lesson_number_by_slot_id: dict[int, int],
                                 pair_placement_vars: dict,
                                 teachers_by_id: dict[int, dict]) -> list:
    """Размещения, неудобные преподавателю по хронотипу: жаворонку поздние
    пары, сове ранние. Список переменных, каждая из которых штрафуется."""
    uncomfortable_vars = []
    for spec in specs:
        if spec.teacher_id is None:
            continue
        chronotype = teachers_by_id.get(spec.teacher_id, {}).get("chronotype")
        for slot_id in slot_ids:
            lesson_number = lesson_number_by_slot_id[slot_id]
            if ((chronotype == Chronotype.EARLY and lesson_number >= CHRONOTYPE_EARLY_BAD_FROM)
                    or (chronotype == Chronotype.LATE
                        and lesson_number <= CHRONOTYPE_LATE_BAD_UNTIL)):
                uncomfortable_vars.append(pair_placement_vars[(spec.event_id, slot_id)])
    return uncomfortable_vars


def collect_free_day_penalties(model: cp_model.CpModel, specs: list[EventSpec],
                               slot_ids: list[int],
                               day_of_week_by_slot_id: dict[int, int],
                               pair_placement_vars: dict, free_days: int) -> list:
    """Режим подготовки к сессии: каждой группе оставить ``free_days`` свободных
    дней в неделю из тех, где ей вообще можно проводить занятия.

    Возвращает переменные нарушения (дней сверх допустимого); нарушение мягкое,
    чтобы нехватка дней не лишала группу занятий. Группа всегда может занять
    хотя бы один день.
    """
    if free_days <= 0:
        return []
    placement_vars_by_group_day: dict[int, dict[int, list]] = defaultdict(lambda: defaultdict(list))
    for spec in specs:
        for group_id in spec.blocking_group_ids:
            for slot_id in slot_ids:
                day = day_of_week_by_slot_id[slot_id]
                if spec.allowed_days and day not in spec.allowed_days:
                    continue
                placement_vars_by_group_day[group_id][day].append(
                    pair_placement_vars[(spec.event_id, slot_id)])

    violation_vars = []
    for group_id, placement_vars_by_day in placement_vars_by_group_day.items():
        allowed_days_count = max(len(placement_vars_by_day) - free_days, 1)
        if len(placement_vars_by_day) <= allowed_days_count:
            continue
        used_day_vars = []
        for day, day_vars in placement_vars_by_day.items():
            used_day_var = model.NewBoolVar(f"day_used_g{group_id}_{day}")
            model.AddMaxEquality(used_day_var, day_vars)
            used_day_vars.append(used_day_var)
        violation_var = model.NewIntVar(
            0, len(used_day_vars) - allowed_days_count, f"free_day_violation_g{group_id}")
        model.Add(violation_var >= sum(used_day_vars) - allowed_days_count)
        violation_vars.append(violation_var)
    return violation_vars


def build_objective(model: cp_model.CpModel, specs: list[EventSpec],
                    slot_ids: list[int], pair_placement_vars: dict,
                    window_vars: list, teacher_overload_vars: list,
                    extra_penalties: Iterable[WeightedTerm] = (),
                    extra_rewards: Iterable[WeightedTerm] = ()) -> None:
    """Собирает и устанавливает целевую функцию (см. докстринг модуля).

    ``extra_penalties`` / ``extra_rewards`` — дополнительные слагаемые
    (вес, переменные) для правил заботы о людях и перепланирования.
    """
    reward_terms = []
    for spec in specs:
        pair_reward = (LECTURE_REWARD if spec.lesson_type == LessonType.LECTURE
                       else REWARD_PLACED)
        for slot_id in slot_ids:
            reward_terms.append(pair_reward * pair_placement_vars[(spec.event_id, slot_id)])

    model.Maximize(
        sum(reward_terms)
        + sum(weight * sum(variables) for weight, variables in extra_rewards)
        - PENALTY_WINDOW * sum(window_vars)
        - PENALTY_TEACHER_OVERLOAD * sum(teacher_overload_vars)
        - sum(weight * sum(variables) for weight, variables in extra_penalties)
    )
