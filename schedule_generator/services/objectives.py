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

from ..enums import Format, LessonType
from .config import (
    LECTURE_REWARD,
    PENALTY_LECTURE_ONSITE_GAP,
    PENALTY_TEACHER_OVERLOAD,
    PENALTY_WINDOW,
    REWARD_PLACED,
)
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

    Отдельно считается штраф за лишние пропуска между лекцией и очным
    занятием одного участника: жёсткое правило (constraints.add_lecture_
    onsite_gap) гарантирует минимум один пропуск, а этот штраф заставляет
    решателя останавливаться на минимуме — каждый дополнительный пустой
    слот между такой парой занятий штрафуется (PENALTY_LECTURE_ONSITE_GAP).
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

    window_vars.extend(collect_lecture_onsite_extra_gap_penalties(
        model, specs, day_of_week_by_slot_id, pair_placement_vars))
    return window_vars


def collect_lecture_onsite_extra_gap_penalties(model: cp_model.CpModel,
                                               specs: list[EventSpec],
                                               day_of_week_by_slot_id: dict[int, int],
                                               pair_placement_vars: dict) -> list:
    """Штраф за ЛИШНИЕ пропуска между лекцией и очным занятием одного участника.

    Жёсткое правило (constraints.add_lecture_onsite_gap) уже гарантирует,
    что подряд лекция и очное не встанут — минимум один пустой слот между
    ними обязателен. Здесь penalзируется каждый дополнительный пустой слот
    между такой парой, чтобы решатель выбирал минимально возможный разрыв
    (одно место занятий не простаивает дольше необходимого).

    Для каждой пары соседних слотов дня (i, i+1): если в i стоит лекция,
    а в i+1 — очное занятие того же участника (или наоборот), то каждый
    пустой слот «глубже» по дню (j > i+1) штрафуется как лишний пропуск:
    extra <= занятость_пары(i, i+1), extra <= 1 - busy_j.
    """
    lecture_specs = [spec for spec in specs if spec.lesson_type == LessonType.LECTURE]
    onsite_specs = [spec for spec in specs if spec.format == Format.ONSITE]
    if not lecture_specs or not onsite_specs:
        return []

    def participants_of(spec: EventSpec) -> set[tuple[str, int]]:
        result = {("group", group_id) for group_id in spec.blocking_group_ids}
        result |= {("student", student_id)
                   for student_id in spec.blocking_student_ids}
        if spec.teacher_id is not None:
            result.add(("teacher", spec.teacher_id))
        return result

    # термы «в слоте у участника стоит лекция / очное занятие»
    lecture_terms_by_participant_slot: dict[tuple[tuple[str, int], int], list] = {}
    onsite_terms_by_participant_slot: dict[tuple[tuple[str, int], int], list] = {}
    all_busy_terms_by_participant_slot: dict[tuple[tuple[str, int], int], list] = {}
    for spec in specs:
        terms = all_busy_terms_by_participant_slot
        lecture_terms = lecture_terms_by_participant_slot if \
            spec.lesson_type == LessonType.LECTURE else None
        onsite_terms = onsite_terms_by_participant_slot if \
            spec.format == Format.ONSITE else None
        for slot_id in day_of_week_by_slot_id:
            placement_var = pair_placement_vars[(spec.event_id, slot_id)]
            for participant in participants_of(spec):
                key = (participant, slot_id)
                terms.setdefault(key, []).append(placement_var)
                if lecture_terms is not None:
                    lecture_terms.setdefault(key, []).append(placement_var)
                if onsite_terms is not None:
                    onsite_terms.setdefault(key, []).append(placement_var)

    # занятость слота участника (busy) — для условия «между ними пусто»
    busy_var_by_participant_slot: dict[tuple[tuple[str, int], int], cp_model.IntVar] = {}
    for key, busy_terms in all_busy_terms_by_participant_slot.items():
        busy_var = model.NewBoolVar(f"lgbusy_{key[0][0]}{key[0][1]}_s{key[1]}")
        model.AddMaxEquality(busy_var, busy_terms)
        busy_var_by_participant_slot[key] = busy_var

    days_of_week = sorted(set(day_of_week_by_slot_id.values()))
    slot_ids_by_day = {
        day: [slot_id for slot_id in sorted(day_of_week_by_slot_id)
              if day_of_week_by_slot_id[slot_id] == day]
        for day in days_of_week
    }

    extra_vars = []
    for day, day_slot_ids in slot_ids_by_day.items():
        for index in range(len(day_slot_ids) - 1):
            slot_i, slot_next = day_slot_ids[index], day_slot_ids[index + 1]
            later_slot_ids = day_slot_ids[index + 2:]
            if not later_slot_ids:
                continue
            participants = {key[0] for key in lecture_terms_by_participant_slot
                            if key[1] == slot_i}
            for participant in participants:
                lecture_at_i = lecture_terms_by_participant_slot.get(
                    (participant, slot_i), [])
                onsite_at_next = onsite_terms_by_participant_slot.get(
                    (participant, slot_next), [])
                onsite_at_i = onsite_terms_by_participant_slot.get(
                    (participant, slot_i), [])
                lecture_at_next = lecture_terms_by_participant_slot.get(
                    (participant, slot_next), [])
                adjacency_terms = []
                for pair_terms in ((lecture_at_i, onsite_at_next),
                                   (onsite_at_i, lecture_at_next)):
                    for left_var in pair_terms[0]:
                        for right_var in pair_terms[1]:
                            adjacent_var = model.NewBoolVar(
                                f"ladj_{participant[0]}{participant[1]}_d{day}_{index}")
                            model.Add(adjacent_var <= left_var)
                            model.Add(adjacent_var <= right_var)
                            model.Add(adjacent_var >= left_var + right_var - 1)
                            adjacency_terms.append(adjacent_var)
                if not adjacency_terms:
                    continue
                for later_slot_id in later_slot_ids:
                    later_busy_var = busy_var_by_participant_slot.get(
                        (participant, later_slot_id))
                    if later_busy_var is None:
                        continue
                    extra_var = model.NewBoolVar(
                        f"lextra_{participant[0]}{participant[1]}_d{day}_{index}")
                    # extra <= min(adjacency_terms) и extra <= 1 - busy_later
                    model.Add(extra_var <= sum(adjacency_terms))
                    model.Add(extra_var <= 1 - later_busy_var)
                    extra_vars.append(extra_var)
    return extra_vars


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
