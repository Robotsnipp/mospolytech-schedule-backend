"""Жёсткие (hard) ограничения модели CP-SAT.

Здесь живут правила «невозможных» комбинаций — решение обязано им следовать.
Каждое ограничение — отдельная функция: легко добавлять новые правила и
легко читать сборку модели в generator.py.

Обозначения:
- x[e, s] — булева переменная «пара события e ставится в слот s»;
- y[e, s, r] — булева переменная «очная пара e в слоте s в аудитории r».
"""

from __future__ import annotations

from ortools.sat.python import cp_model

from ..enums import Format
from .dto import EventSpec


def add_lecture_single_slot(model: cp_model.CpModel, specs: list[EventSpec],
                            slot_ids: list[int], x: dict) -> None:
    """Лекция одного потока идёт строго в один слот недели:
    все группы потока слушают её одновременно; аудитория не нужна."""
    for sp in specs:
        if sp.stream_id is not None:
            model.AddAtMostOne(x[(sp.event_id, sid)] for sid in slot_ids)


def add_room_exclusivity(model: cp_model.CpModel, y: dict) -> None:
    """В одной аудитории в одном слоте — не более одного очного занятия."""
    by_slot_room: dict[tuple[int, int], list] = {}
    for (_eid, sid, rid), v in y.items():
        by_slot_room.setdefault((sid, rid), []).append(v)
    for vs in by_slot_room.values():
        if len(vs) > 1:
            model.AddAtMostOne(vs)


def add_attendee_exclusivity(model: cp_model.CpModel, specs: list[EventSpec],
                             slot_ids: list[int], x: dict) -> None:
    """Учебная группа / студент: не более одного занятия (любого формата)
    в слоте. ПД блокирует только студентов проекта, практики/лекции —
    целиком учебные группы."""
    by_slot_attendee: dict[tuple[str, int, int], list] = {}
    for sp in specs:
        for sid in slot_ids:
            term = x[(sp.event_id, sid)]
            for gid in sp.blocking_group_ids:
                by_slot_attendee.setdefault(("g", gid, sid), []).append(term)
            for stid in sp.blocking_student_ids:
                by_slot_attendee.setdefault(("s", stid, sid), []).append(term)
    for vs in by_slot_attendee.values():
        if len(vs) > 1:
            model.AddAtMostOne(vs)


def add_teacher_exclusivity(model: cp_model.CpModel, specs: list[EventSpec],
                            slot_ids: list[int], x: dict) -> None:
    """Преподаватель ведёт не более одного занятия в слоте
    (перегрузка по дням — мягкое ограничение, см. objectives.py)."""
    by_slot_teacher: dict[int, dict[int, list]] = {}
    for sp in specs:
        if sp.teacher_id is None:
            continue
        for sid in slot_ids:
            by_slot_teacher.setdefault(sp.teacher_id, {}).setdefault(
                sid, []).append(x[(sp.event_id, sid)])
    for slot_map in by_slot_teacher.values():
        for vs in slot_map.values():
            if len(vs) > 1:
                model.AddAtMostOne(vs)


def add_event_pair_vars(model: cp_model.CpModel, specs: list[EventSpec],
                        slot_ids: list[int], x: dict) -> dict[int, cp_model.IntVar]:
    """Для каждого события: placed[e] = число поставленных пар и
    short[e] >= pairs_per_week - placed (недостающие пары).

    Возвращает {event_id: short_var}; short-переменные нужны solution.py
    для определения неполностью запланированных событий.
    """
    short_by_event: dict[int, cp_model.IntVar] = {}
    for sp in specs:
        placed = model.NewIntVar(0, sp.pairs_per_week, f"placed_{sp.event_id}")
        model.Add(placed == sum(x[(sp.event_id, sid)] for sid in slot_ids))
        short = model.NewIntVar(0, sp.pairs_per_week, f"short_{sp.event_id}")
        model.Add(short >= sp.pairs_per_week - placed)
        short_by_event[sp.event_id] = short
    return short_by_event


def link_onsite_room_vars(model: cp_model.CpModel, specs: list[EventSpec],
                          slot_ids: list[int], x: dict, y: dict,
                          onsite_ok_rooms: dict[int, list[int]]) -> None:
    """Связывает x и y для очных событий:
    - если ни одна аудитория не подходит по вместимости — пары невозможны (x=0);
    - каждая поставленная очная пара получает ровно одну аудиторию."""
    for sp in specs:
        if sp.fmt != Format.ONSITE:
            continue
        ok_rooms = onsite_ok_rooms.get(sp.event_id, [])
        if not ok_rooms:
            for sid in slot_ids:
                model.Add(x[(sp.event_id, sid)] == 0)
        else:
            for sid in slot_ids:
                pair_y = [y[(sp.event_id, sid, rid)] for rid in ok_rooms]
                model.Add(sum(pair_y) == x[(sp.event_id, sid)])
