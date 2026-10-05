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
def persist_result(*, run, gen: GenerationResult, slots, rooms, events,
                   skipped: list[tuple], clear_previous: bool):
    """Атомарно записывает результат запуска.

    Шаги (порядок = смысл):
    1. при clear_previous — чистим пары предыдущих запусков семестра;
    2. bulk_create ScheduledClass по присваиваниям решателя;
    3. GenerationIssue: пропущенные события (skipped) и неполностью
       запланированные (unscheduled);
    4. финализируем статус/метрики GenerationRun.
    """
    slot_by_id = {s.id: s for s in slots}
    room_ids = {r.id for r in rooms}

    if clear_previous:
        ScheduledClass.objects.filter(run__semester=run.semester).delete()

    ScheduledClass.objects.bulk_create([
        ScheduledClass(
            event_id=a["event_id"],
            slot=slot_by_id[a["slot_id"]],
            room_id=a["room_id"] if a["room_id"] in room_ids else None,
            run=run,
        )
        for a in gen.assignments
    ])

    for ev, reason in skipped:
        GenerationIssue.objects.create(
            run=run, event=None, level="WARNING",
            text=f"Событие пропущено ({reason}): #{ev.pk} {ev}",
        )
    unscheduled_ids = set(gen.unscheduled_event_ids)
    if unscheduled_ids:
        ev_by_id = {e.id: e for e in events.filter(id__in=unscheduled_ids)}
        for eid in sorted(unscheduled_ids):
            GenerationIssue.objects.create(
                run=run, event=ev_by_id.get(eid), level="WARNING",
                text=f"Не все обязательные пары события запланированы: #{eid}",
            )

    run.status = gen.status
    run.objective_value = gen.objective_value
    run.solve_time_seconds = gen.solve_time
    run.message = gen.message
    run.save()
    return run
