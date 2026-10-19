"""Свободные аудитории в заданный момент (фича «где позаниматься»)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from django.utils import timezone

from ..models import Room, ScheduledClass, TimeSlot
from .schedule_queries import resolve_run

# Сетка пар задана во времени университета, а не в UTC из настроек проекта.
UNIVERSITY_TIMEZONE = ZoneInfo("Europe/Moscow")


def university_now() -> datetime:
    """Текущий момент во времени университета."""
    return timezone.now().astimezone(UNIVERSITY_TIMEZONE)


def slot_at(week_pattern, moment: datetime) -> TimeSlot | None:
    """Слот шаблона, идущий в момент ``moment`` (по дню недели и времени пары).

    Слоты без заполненного времени начала и конца не участвуют: по ним
    нельзя определить, идёт ли пара. Момент переводится во время университета.
    """
    moment = moment.astimezone(UNIVERSITY_TIMEZONE)
    return (TimeSlot.objects
            .filter(week_pattern=week_pattern, day_of_week=moment.weekday(),
                    start_time__lte=moment.time(), end_time__gt=moment.time())
            .first())


def free_rooms(*, semester, slot: TimeSlot, module=None, run_id=None,
               min_capacity: int | None = None, location_id: int | None = None):
    """Активные аудитории, не занятые в слоте по выбранному запуску.

    Занятость считается по дню недели и номеру пары, чтобы запуск с другим
    шаблоном недели не давал ложных «свободно». Если у периода нет запуска,
    свободными считаются все подходящие аудитории.
    """
    rooms = Room.objects.filter(is_active=True).select_related("building", "building__location")
    if min_capacity is not None:
        rooms = rooms.filter(capacity__gte=min_capacity)
    if location_id is not None:
        rooms = rooms.filter(building__location_id=location_id)

    run = resolve_run(semester, module, run_id)
    if run is not None:
        occupied_room_ids = (ScheduledClass.objects
                             .filter(run=run, room__isnull=False,
                                     slot__day_of_week=slot.day_of_week,
                                     slot__lesson_number=slot.lesson_number)
                             .values_list("room_id", flat=True))
        rooms = rooms.exclude(id__in=occupied_room_ids)
    return rooms.order_by("name")
