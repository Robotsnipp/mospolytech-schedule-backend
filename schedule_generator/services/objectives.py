"""Полнота размещения, затем приоритет лекций, затем удобство людей.

Окно — ожидание между последовательными занятиями сверх обычного перерыва.
У студента вместе учитываются пары группы/потока и его ПД, у преподавателя —
его пары и фиксированная ПД. Одинаковые расписания объединяются в профиль
с весом по числу людей.
"""

from __future__ import annotations

from collections import Counter, defaultdict
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
    PENALTY_TEACHER_OVERLOAD,
    PENALTY_WINDOW,
    WINDOW_FREE_MINUTES,
)
from .dto import EventSpec
from .wellbeing import max_day_fatigue

WeightedTerm = tuple[int, Sequence]


def collect_teacher_overload_penalties(
    model: cp_model.CpModel,
    specs: list[EventSpec],
    day_of_week_by_slot_id: dict[int, int],
    pair_placement_vars: dict,
    teachers_by_id: dict[int, dict],
) -> list:
    """Штраф за превышение max_pairs_per_day генерируемых пар преподавателя."""
    vars_by_teacher_day: dict[tuple[int, int], list] = {}
    for spec in specs:
        if spec.teacher_id is None:
            continue
        for slot_id in day_of_week_by_slot_id:
            vars_by_teacher_day.setdefault(
                (spec.teacher_id, day_of_week_by_slot_id[slot_id]), []
            ).append(pair_placement_vars[(spec.event_id, slot_id)])
    overload_vars = []
    for (teacher_id, day), terms in vars_by_teacher_day.items():
        limit = teachers_by_id.get(teacher_id, {}).get("max_pairs_per_day")
        if limit and len(terms) > limit:
            overload = model.NewIntVar(0, len(terms), f"overload_{teacher_id}_{day}")
            model.Add(overload >= sum(terms) - limit)
            overload_vars.append(overload)
    return overload_vars


def _attendance_profiles(specs, fixed_pd):
    events_by_person = defaultdict(set)
    fixed_by_person = defaultdict(set)
    for spec in specs:
        students = spec.attending_student_ids or spec.blocking_student_ids
        for student_id in students:
            events_by_person["student", student_id].add(spec.event_id)
        # В старых/ручных сценариях без состава студентов оцениваем группу.
        if not students:
            for group_id in spec.blocking_group_ids:
                events_by_person["group", group_id].add(spec.event_id)
        if spec.teacher_id is not None:
            events_by_person["teacher", spec.teacher_id].add(spec.event_id)
    for session in fixed_pd:
        interval = (session.day_of_week, session.start_minute, session.end_minute)
        for student_id in session.student_ids:
            fixed_by_person["student", student_id].add(interval)
        fixed_by_person["teacher", session.teacher_id].add(interval)
    profiles = Counter()
    for person in events_by_person.keys() | fixed_by_person.keys():
        # Датированные ПД разных недель могут перекрываться в консервативном
        # недельном шаблоне. Для ожидания нужен их временной союз, а не два конца.
        intervals = []
        for day, start, end in sorted(fixed_by_person[person]):
            if intervals and intervals[-1][0] == day and start <= intervals[-1][2]:
                previous_day, previous_start, previous_end = intervals[-1]
                intervals[-1] = (previous_day, previous_start, max(previous_end, end))
            else:
                intervals.append((day, start, end))
        profiles[
            tuple(sorted(events_by_person[person])),
            tuple(intervals),
        ] += 1
    return profiles


def collect_window_penalties(
    model: cp_model.CpModel,
    specs: list[EventSpec],
    slot_ids: list[int],
    day_of_week_by_slot_id: dict[int, int],
    pair_placement_vars: dict,
    *,
    slots=(),
    fixed_pd=(),
) -> list:
    """Точный штраф в человеко-минутах между последовательными занятиями.

    Без времени слотов в старых сценариях используется число пропущенных
    слотов. При временной сетке перерыв до WINDOW_FREE_MINUTES не штрафуется.
    """
    slots_by_id = {slot.id: slot for slot in slots}
    slots_by_day = defaultdict(list)
    for slot_id in slot_ids:
        slots_by_day[day_of_week_by_slot_id[slot_id]].append(slot_id)
    penalties = []
    profiles = _attendance_profiles(specs, fixed_pd)
    for profile_index, ((event_ids, fixed_intervals), people_count) in enumerate(
        sorted(profiles.items())
    ):
        for day in sorted(
            slots_by_day.keys() | {interval[0] for interval in fixed_intervals}
        ):
            day_slots = slots_by_day[day]
            timed = all(
                slot_id in slots_by_id
                and slots_by_id[slot_id].start_time is not None
                and slots_by_id[slot_id].end_time is not None
                for slot_id in day_slots
            )
            activities = []
            for index, slot_id in enumerate(day_slots):
                if not event_ids:
                    break
                if timed:
                    slot = slots_by_id[slot_id]
                    start = slot.start_time.hour * 60 + slot.start_time.minute
                    end = slot.end_time.hour * 60 + slot.end_time.minute
                else:
                    start, end = index, index + 1
                busy = model.NewBoolVar(f"busy_profile{profile_index}_{day}_{slot_id}")
                model.AddMaxEquality(
                    busy,
                    [pair_placement_vars[event_id, slot_id] for event_id in event_ids],
                )
                activities.append((start, end, busy))
            for fixed_day, start, end in fixed_intervals:
                if fixed_day == day:
                    if not timed:
                        raise ValueError(
                            "Для оценки окон возле ПД нужно время начала и конца слотов."
                        )
                    activities.append((start, end, model.NewConstant(1)))
            activities.sort(key=lambda activity: (activity[0], activity[1]))
            free_break = WINDOW_FREE_MINUTES if timed else 0
            for i, (_, end, before) in enumerate(activities):
                for j in range(i + 1, len(activities)):
                    start, _, after = activities[j]
                    wait = max(0, start - end - free_break)
                    if not wait:
                        continue
                    middle = [activity[2] for activity in activities[i + 1 : j]]
                    gap = model.NewBoolVar(f"gap_profile{profile_index}_{day}_{i}_{j}")
                    model.Add(gap <= before)
                    model.Add(gap <= after)
                    for busy in middle:
                        model.Add(gap + busy <= 1)
                    model.Add(gap >= before + after - sum(middle) - 1)
                    cost = model.NewIntVar(
                        0,
                        wait * people_count,
                        f"wait_profile{profile_index}_{day}_{i}_{j}",
                    )
                    model.Add(cost == wait * people_count * gap)
                    penalties.append(cost)
    return penalties


def build_objective(
    model: cp_model.CpModel,
    specs: list[EventSpec],
    slot_ids: list[int],
    pair_placement_vars: dict,
    window_vars: list,
    teacher_overload_vars: list,
    extra_penalties: Iterable[WeightedTerm] = (),
    extra_rewards: Iterable[WeightedTerm] = (),
) -> None:
    """Строгий порядок: число пар → число лекций → мягкие штрафы.

    Верхние границы штрафных переменных задают веса: уменьшение окон не
    компенсирует потерю пары. При равном числе пар сохраняется приоритет лекций.
    """
    extra_penalties, extra_rewards = list(extra_penalties), list(extra_rewards)
    quality = PENALTY_WINDOW * sum(window_vars) + PENALTY_TEACHER_OVERLOAD * sum(
        teacher_overload_vars
    )
    quality_bound = PENALTY_WINDOW * sum(
        max(v.proto.domain) for v in window_vars
    ) + PENALTY_TEACHER_OVERLOAD * sum(
        max(v.proto.domain) for v in teacher_overload_vars
    )
    quality += sum(weight * sum(variables) for weight, variables in extra_penalties)
    quality_bound += sum(weight * sum(max(v.proto.domain) for v in variables)
                         for weight, variables in extra_penalties)
    quality_bound += sum(weight * sum(max(v.proto.domain) for v in variables)
                         for weight, variables in extra_rewards)
    rewards = sum(weight * sum(variables) for weight, variables in extra_rewards)
    lecture_bound = sum(
        spec.pairs_per_week for spec in specs if spec.lesson_type == LessonType.LECTURE
    )
    lecture_weight = quality_bound + 1
    pair_weight = (lecture_bound + 1) * lecture_weight
    placed = sum(pair_placement_vars.values())
    lectures = sum(
        pair_placement_vars[spec.event_id, slot_id]
        for spec in specs
        if spec.lesson_type == LessonType.LECTURE
        for slot_id in slot_ids
    )
    model.Maximize(pair_weight * placed + lecture_weight * lectures - quality + rewards)


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
