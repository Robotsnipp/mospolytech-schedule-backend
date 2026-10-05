"""Сохранение результата генерации в БД (единица транзакции).

Модуль знает только про модели журнала генерации; ничего не знает ни про
решатель, ни про HTTP. На вход — плоский GenerationResult и контекст
загрузки данных (slots/rooms/events), на выход — обновлённый GenerationRun.
"""

from __future__ import annotations

from django.db import transaction

from ..models import GenerationIssue, ScheduledClass
from .dto import GenerationResult


@transaction.atomic
def persist_result(*, run, generation_result: GenerationResult, slots, rooms, events,
                   skipped: list[tuple], clear_previous: bool):
    """Атомарно записывает результат запуска.

    Шаги (порядок = смысл):
    1. при clear_previous — чистим пары предыдущих запусков семестра;
    2. bulk_create ScheduledClass по присваиваниям решателя;
    3. GenerationIssue: пропущенные события (skipped) и неполностью
       запланированные (unscheduled);
    4. финализируем статус/метрики GenerationRun.
    """
    slot_by_id = {slot.id: slot for slot in slots}
    known_room_ids = {room.id for room in rooms}

    if clear_previous:
        ScheduledClass.objects.filter(run__semester=run.semester).delete()

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
        for unscheduled_event_id in sorted(unscheduled_event_ids):
            GenerationIssue.objects.create(
                run=run, event=event_by_id.get(unscheduled_event_id), level="WARNING",
                text=f"Не все обязательные пары события запланированы: "
                     f"#{unscheduled_event_id}",
            )

    run.status = generation_result.status
    run.objective_value = generation_result.objective_value
    run.solve_time_seconds = generation_result.solve_time
    run.message = generation_result.message
    run.save()
    return run
