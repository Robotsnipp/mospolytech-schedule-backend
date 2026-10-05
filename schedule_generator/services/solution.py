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
                   pair_placement_vars: dict, onsite_room_choice_vars: dict) -> GenerationResult:
    """Формирует GenerationResult по решению CP-SAT.

    Статусы: SOLVED — всё встало; PARTIAL — есть unscheduled; INFEASIBLE —
    решения нет вовсе.
    """
    assignments: list[dict] = []
    unscheduled_event_ids: list[int] = []

    if not feasible:
        return GenerationResult(
            status="INFEASIBLE",
            objective_value=None,
            solve_time=0.0,  # заполняет вызывающий (generator.generate_schedule)
            assignments=[],
            unscheduled_event_ids=[spec.event_id for spec in specs],
            message=f"status={status_name}, assigned=0",
        )

    for spec in specs:
        placed_pairs_count = 0
        for slot_id in _slot_ids_of_event(pair_placement_vars, spec):
            if solver.Value(pair_placement_vars[(spec.event_id, slot_id)]):
                room_id = None
                if spec.format == Format.ONSITE:
                    for candidate_room_id in room_ids:
                        choice_key = (spec.event_id, slot_id, candidate_room_id)
                        if (choice_key in onsite_room_choice_vars
                                and solver.Value(onsite_room_choice_vars[choice_key])):
                            room_id = candidate_room_id
                            break
                assignments.append({
                    "event_id": spec.event_id, "slot_id": slot_id, "room_id": room_id,
                })
                placed_pairs_count += 1
        if placed_pairs_count < spec.pairs_per_week:
            unscheduled_event_ids.append(spec.event_id)

    result_status = "PARTIAL" if unscheduled_event_ids else "SOLVED"
    return GenerationResult(
        status=result_status,
        objective_value=solver.ObjectiveValue(),
        solve_time=0.0,  # заполняет вызывающий (generator.generate_schedule)
        assignments=assignments,
        unscheduled_event_ids=unscheduled_event_ids,
        message=f"status={status_name}, assigned={len(assignments)}",
    )


def _slot_ids_of_event(pair_placement_vars: dict, spec: EventSpec):
    """Слоты события в порядке обхода ключей pair_placement_vars (slot_ids
    отсортированы при построении модели — порядок сохраняется)."""
    return [key[1] for key in pair_placement_vars if key[0] == spec.event_id]
