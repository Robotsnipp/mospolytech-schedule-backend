"""Мягкие (soft) ограничения и целевая функция модели CP-SAT.

Модель реализуема всегда: любое «неудобство» штрафуется, а не делает задачу
неразрешимой. Здесь собираются штрафные переменные и итоговая цель.

Целевая функция (максимизация):
    + награда за каждую поставленную пару (лекции — с повышенным весом)
    - штраф за «окна» у групп/студентов
    - штраф за перегрузку преподавателей по парам в день

Недостающие обязательные пары (short-переменные) в целевую НЕ входят:
placed == sum(x), поэтому штраф за них эквивалентен награде за placed.
"""

from __future__ import annotations

from ortools.sat.python import cp_model

from ..enums import LessonType
from .config import LECTURE_REWARD, PENALTY_TEACHER_OVERLOAD, PENALTY_WINDOW, REWARD_PLACED
from .dto import EventSpec


def collect_teacher_overload_penalties(model: cp_model.CpModel, specs: list[EventSpec],
                                       slot_day: dict[int, int], x: dict,
                                       teachers_by_id: dict[int, dict]) -> list:
    """Штраф за превышение max_pairs_per_day преподавателя (мягко)."""
    per_teacher_day: dict[tuple[int, int], list] = {}
    for sp in specs:
        if sp.teacher_id is None:
            continue
        for sid in slot_day:
            per_teacher_day.setdefault((sp.teacher_id, slot_day[sid]), []).append(
                x[(sp.event_id, sid)]
            )

    over_vars = []
    for (tid, day), vs in per_teacher_day.items():
        max_per_day = teachers_by_id.get(tid, {}).get("max_per_day")
        if max_per_day and len(vs) > max_per_day:
            over = model.NewIntVar(0, len(vs), f"tover_{tid}_{day}")
            model.Add(over >= sum(vs) - max_per_day)
            over_vars.append(over)
    return over_vars


def collect_window_penalties(model: cp_model.CpModel, specs: list[EventSpec],
                             slot_ids: list[int], slot_day: dict[int, int],
                             x: dict) -> list:
    """Штраф за «окна»: пропущенный слот между двумя занятыми в течение дня.

    Считается для учебных групп (занятия на группу/поток) и отдельно для
    студентов (ПД блокирует конкретных студентов, а не их учебные группы).
    """
    win_penalty_vars = []
    days = sorted(set(slot_day.values()))
    # slot_ids упорядочены по (day, lesson_number) — см. generator.py
    slots_by_day = {d: [sid for sid in slot_ids if slot_day[sid] == d] for d in days}

    occ_by_attendee: dict[tuple[str, int], dict[int, list]] = {}
    for sp in specs:
        for sid in slot_ids:
            term = x[(sp.event_id, sid)]
            for gid in sp.blocking_group_ids:
                occ_by_attendee.setdefault(("g", gid), {}).setdefault(sid, []).append(term)
            for stid in sp.blocking_student_ids:
                occ_by_attendee.setdefault(("s", stid), {}).setdefault(sid, []).append(term)

    for kind_id, occ in occ_by_attendee.items():
        for d in days:
            day_slots = slots_by_day[d]
            busy = []
            for i, sid in enumerate(day_slots):
                b = model.NewBoolVar(f"busy_{kind_id[0]}{kind_id[1]}_{d}_{i}")
                terms = occ.get(sid, [])
                if terms:
                    model.AddMaxEquality(b, terms)
                else:
                    model.Add(b == 0)
                busy.append(b)
            # окно: busy[i]==1 и busy[j]==1 (j>i+1), всё между пусто
            for i in range(len(busy)):
                for j in range(i + 2, len(busy)):
                    gap = model.NewBoolVar(f"gap_{kind_id[0]}{kind_id[1]}_{d}_{i}_{j}")
                    mid = busy[i + 1:j]
                    # gap <= busy_i, gap <= busy_j, gap <= 1 - mid_k (все k)
                    model.Add(gap <= busy[i])
                    model.Add(gap <= busy[j])
                    for m in mid:
                        model.Add(gap <= 1 - m)
                    win_penalty_vars.append(gap)
    return win_penalty_vars


def build_objective(model: cp_model.CpModel, specs: list[EventSpec],
                    slot_ids: list[int], x: dict,
                    window_vars: list, teacher_over_vars: list) -> None:
    """Собирает и устанавливает целевую функцию (см. докстринг модуля)."""
    reward_terms = []
    for sp in specs:
        weight = LECTURE_REWARD if sp.lesson_type == LessonType.LECTURE else REWARD_PLACED
        for sid in slot_ids:
            reward_terms.append(weight * x[(sp.event_id, sid)])

    model.Maximize(
        sum(reward_terms)
        - PENALTY_WINDOW * sum(window_vars)
        - PENALTY_TEACHER_OVERLOAD * sum(teacher_over_vars)
    )
