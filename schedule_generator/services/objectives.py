"""Полнота размещения, затем приоритет лекций, затем удобство людей.

Окно — ожидание между последовательными занятиями сверх обычного перерыва.
У студента вместе учитываются пары группы/потока и его ПД, у преподавателя —
его пары и фиксированная ПД. Одинаковые расписания объединяются в профиль
с весом по числу людей.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from ortools.sat.python import cp_model

from ..enums import LessonType
from .config import PENALTY_TEACHER_OVERLOAD, PENALTY_WINDOW, WINDOW_FREE_MINUTES
from .dto import EventSpec


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
) -> None:
    """Строгий порядок: число пар → число лекций → мягкие штрафы.

    Верхние границы штрафных переменных задают веса: уменьшение окон не
    компенсирует потерю пары. При равном числе пар сохраняется приоритет лекций.
    """
    quality = PENALTY_WINDOW * sum(window_vars) + PENALTY_TEACHER_OVERLOAD * sum(
        teacher_overload_vars
    )
    quality_bound = PENALTY_WINDOW * sum(
        max(v.proto.domain) for v in window_vars
    ) + PENALTY_TEACHER_OVERLOAD * sum(
        max(v.proto.domain) for v in teacher_overload_vars
    )
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
    model.Maximize(pair_weight * placed + lecture_weight * lectures - quality)
