"""Сохранение результата генерации в БД (единица транзакции).

Модуль знает только про модели журнала генерации; ничего не знает ни про
решатель, ни про HTTP. На вход — плоский GenerationResult и контекст
загрузки данных (slots/rooms/events), на выход — обновлённый GenerationRun.
"""

from __future__ import annotations

from django.db import transaction

from ..models import GenerationIssue, ScheduledClass
from .diagnostics import build_shortage_explanations
from .dto import EventSpec, GenerationResult


@transaction.atomic
def persist_result(*, run, generation_result: GenerationResult, slots, rooms, events,
                   specs: list[EventSpec], skipped: list[tuple], clear_previous: bool):
    """Атомарно записывает результат запуска.

    Шаги (порядок = смысл):
    1. при clear_previous — чистим пары предыдущих запусков семестра;
    2. bulk_create ScheduledClass по присваиваниям решателя;
    3. GenerationIssue: пропущенные события (skipped), неполностью
       запланированные (unscheduled) — каждое с объяснением «чего не
       хватает» (services/diagnostics.py) + одна сводная ERROR-проблема
       со всеми уникальными причинами дефицита;
    4. финализируем статус/метрики GenerationRun.
    """
    slot_by_id = {slot.id: slot for slot in slots}
    known_room_ids = {room.id for room in rooms}

    if clear_previous and generation_result.status == "SOLVED" and not skipped:
        ScheduledClass.objects.filter(run__semester=run.semester, run__module=run.module).delete()

    ScheduledClass.objects.bulk_create([
        ScheduledClass(
            event_id=assignment["event_id"],
            slot=slot_by_id[assignment["slot_id"]],
            room_id=(assignment["room_id"]
                     if assignment["room_id"] in known_room_ids else None),
            run=run,
        )
        for assignment in generation_result.assignments
    ])

    for skipped_event, reason in skipped:
        GenerationIssue.objects.create(
            run=run, event=None, level="WARNING",
            text=f"Событие пропущено ({reason}): #{skipped_event.pk} {skipped_event}",
        )
    unscheduled_event_ids = set(generation_result.unscheduled_event_ids)
    if unscheduled_event_ids:
        event_by_id = {event.id: event
                       for event in events.filter(id__in=unscheduled_event_ids)}
        # PARTIAL и INFEASIBLE обрабатывает diagnostics.build_shortage_explanations:
        # при INFEASIBLE assignments пусты, занятость достраивается эвристикой
        explanations = build_shortage_explanations(
            specs=specs, generation_result=generation_result,
            slots=slots, rooms=rooms)
        for unscheduled_event_id in sorted(unscheduled_event_ids):
            event = event_by_id.get(unscheduled_event_id)
            explanation = explanations.get(unscheduled_event_id, "")
            GenerationIssue.objects.create(
                run=run, event=event, level="WARNING",
                text=(f"Не все обязательные пары события запланированы: "
                      f"#{unscheduled_event_id}"
                      + (f". {explanation}" if explanation else "")),
            )
        unique_reasons = sorted(set(explanations.values()))
        if unique_reasons:
            GenerationIssue.objects.create(
                run=run, event=None, level="ERROR",
                text="Чего не хватает: " + " | ".join(unique_reasons))

    run.status = generation_result.status
    run.objective_value = generation_result.objective_value
    run.solve_time_seconds = generation_result.solve_time
    run.message = generation_result.message
    run.save()
    return run
