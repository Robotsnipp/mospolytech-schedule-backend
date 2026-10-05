"""Извлечение плоского результата из решения решателя (без ORM).

На вход — уже решённая модель и переменные; на выход — GenerationResult:
список присваиваний {event_id, slot_id, room_id} и события, которые нельзя
запланировать полностью (unscheduled -> GenerationIssue на уровне сервиса).
"""

from __future__ import annotations

from ortools.sat.python import cp_model

from ..enums import Format
from .dto import EventSpec, GenerationResult


def extract_result(solver: cp_model.CpSolver, status_name: str, feasible: bool,
                   specs: list[EventSpec], room_ids: list[int],
                   x: dict, y: dict) -> GenerationResult:
    """Формирует GenerationResult по решению CP-SAT.

    Статусы: SOLVED — всё встало; PARTIAL — есть unscheduled; INFEASIBLE —
    решения нет вовсе.
    """
    assignments: list[dict] = []
    unscheduled: list[int] = []

    if not feasible:
        return GenerationResult(
            status="INFEASIBLE",
            objective_value=None,
            solve_time=0.0,  # заполняет вызывающий (generator.solve)
            assignments=[],
            unscheduled_event_ids=[sp.event_id for sp in specs],
            message=f"status={status_name}, assigned=0",
        )

    for sp in specs:
        placed_count = 0
        for sid in (s for s in _all_slot_ids(x, sp)):
            if solver.Value(x[(sp.event_id, sid)]):
                room_id = None
                if sp.fmt == Format.ONSITE:
                    for rid in room_ids:
                        key = (sp.event_id, sid, rid)
                        if key in y and solver.Value(y[key]):
                            room_id = rid
                            break
                assignments.append({
                    "event_id": sp.event_id, "slot_id": sid, "room_id": room_id,
                })
                placed_count += 1
        if placed_count < sp.pairs_per_week:
            unscheduled.append(sp.event_id)

    result_status = "PARTIAL" if unscheduled else "SOLVED"
    return GenerationResult(
        status=result_status,
        objective_value=solver.ObjectiveValue(),
        solve_time=0.0,  # заполняет вызывающий (generator.solve)
        assignments=assignments,
        unscheduled_event_ids=unscheduled,
        message=f"status={status_name}, assigned={len(assignments)}",
    )


def _all_slot_ids(x: dict, sp: EventSpec):
    """Слоты события в порядке обхода ключей x (slot_ids отсортированы при
    построении модели — порядок сохраняется)."""
    return [key[1] for key in x if key[0] == sp.event_id]
