"""Жёсткие (hard) ограничения модели CP-SAT.

Здесь живут правила «невозможных» комбинаций — решение обязано им следовать.
Каждое ограничение — отдельная функция: легко добавлять новые правила и
легко читать сборку модели в generator.py.

Обозначения:
- pair_placement_vars[event_id, slot_id] — булева переменная «пара события
  ставится в слот»;
- onsite_room_choice_vars[event_id, slot_id, room_id] — булева переменная
  «очная пара в слоте в данной аудитории».
"""

from __future__ import annotations

from ortools.sat.python import cp_model

from ..enums import Format
from .dto import EventSpec


def add_lecture_single_slot(model: cp_model.CpModel, specs: list[EventSpec],
                            slot_ids: list[int], pair_placement_vars: dict) -> None:
    """Лекция одного потока идёт строго в один слот недели:
    все группы потока слушают её одновременно; аудитория не нужна."""
    for spec in specs:
        if spec.stream_id is not None:
            model.AddAtMostOne(
                pair_placement_vars[(spec.event_id, slot_id)] for slot_id in slot_ids
            )


def add_room_exclusivity(model: cp_model.CpModel, onsite_room_choice_vars: dict) -> None:
    """В одной аудитории в одном слоте — не более одного очного занятия."""
    vars_by_slot_room: dict[tuple[int, int], list] = {}
    for (_event_id, slot_id, room_id), var in onsite_room_choice_vars.items():
        vars_by_slot_room.setdefault((slot_id, room_id), []).append(var)
    for slot_room_vars in vars_by_slot_room.values():
        if len(slot_room_vars) > 1:
            model.AddAtMostOne(slot_room_vars)


def add_attendee_exclusivity(model: cp_model.CpModel, specs: list[EventSpec],
                             slot_ids: list[int], pair_placement_vars: dict) -> None:
    """Учебная группа / студент: не более одного занятия (любого формата)
    в слоте. ПД блокирует только студентов проекта, практики/лекции —
    целиком учебные группы."""
    vars_by_slot_attendee: dict[tuple[str, int, int], list] = {}
    for spec in specs:
        for slot_id in slot_ids:
            placement_var = pair_placement_vars[(spec.event_id, slot_id)]
            for group_id in spec.blocking_group_ids:
                vars_by_slot_attendee.setdefault(
                    ("group", group_id, slot_id), []).append(placement_var)
            for student_id in spec.blocking_student_ids:
                vars_by_slot_attendee.setdefault(
                    ("student", student_id, slot_id), []).append(placement_var)
    for attendee_vars in vars_by_slot_attendee.values():
        if len(attendee_vars) > 1:
            model.AddAtMostOne(attendee_vars)


def add_teacher_exclusivity(model: cp_model.CpModel, specs: list[EventSpec],
                            slot_ids: list[int], pair_placement_vars: dict) -> None:
    """Преподаватель ведёт не более одного занятия в слоте
    (перегрузка по дням — мягкое ограничение, см. objectives.py)."""
    vars_by_teacher_slot: dict[int, dict[int, list]] = {}
    for spec in specs:
        if spec.teacher_id is None:
            continue
        for slot_id in slot_ids:
            vars_by_teacher_slot.setdefault(spec.teacher_id, {}).setdefault(
                slot_id, []).append(pair_placement_vars[(spec.event_id, slot_id)])
    for slot_map in vars_by_teacher_slot.values():
        for teacher_slot_vars in slot_map.values():
            if len(teacher_slot_vars) > 1:
                model.AddAtMostOne(teacher_slot_vars)


def add_event_pair_vars(model: cp_model.CpModel, specs: list[EventSpec],
                        slot_ids: list[int], pair_placement_vars: dict) -> dict[int, cp_model.IntVar]:
    """Для каждого события: placed_pairs[e] = число поставленных пар и
    missing_pairs[e] >= pairs_per_week - placed_pairs (недостающие пары).

    Возвращает {event_id: missing_pairs_var}; missing-переменные описывают
    неполностью запланированные события (используются целевой функцией).
    """
    missing_pairs_by_event: dict[int, cp_model.IntVar] = {}
    for spec in specs:
        placed_pairs = model.NewIntVar(0, spec.pairs_per_week,
                                       f"placed_{spec.event_id}")
        model.Add(placed_pairs == sum(
            pair_placement_vars[(spec.event_id, slot_id)] for slot_id in slot_ids))
        missing_pairs = model.NewIntVar(0, spec.pairs_per_week,
                                        f"missing_{spec.event_id}")
        model.Add(missing_pairs >= spec.pairs_per_week - placed_pairs)
        missing_pairs_by_event[spec.event_id] = missing_pairs
    return missing_pairs_by_event


def link_onsite_room_vars(model: cp_model.CpModel, specs: list[EventSpec],
                          slot_ids: list[int], pair_placement_vars: dict,
                          onsite_room_choice_vars: dict,
                          suitable_rooms_by_event_id: dict[int, list[int]]) -> None:
    """Связывает переменные размещения и выбора аудитории для очных событий:
    - если ни одна аудитория не подходит по вместимости — пары невозможны;
    - каждая поставленная очная пара получает ровно одну аудиторию."""
    for spec in specs:
        if spec.format != Format.ONSITE:
            continue
        suitable_room_ids = suitable_rooms_by_event_id.get(spec.event_id, [])
        if not suitable_room_ids:
            for slot_id in slot_ids:
                model.Add(pair_placement_vars[(spec.event_id, slot_id)] == 0)
        else:
            for slot_id in slot_ids:
                room_choice_vars = [
                    onsite_room_choice_vars[(spec.event_id, slot_id, room_id)]
                    for room_id in suitable_room_ids
                ]
                model.Add(sum(room_choice_vars) ==
                          pair_placement_vars[(spec.event_id, slot_id)])
