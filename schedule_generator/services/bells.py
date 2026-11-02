"""Explicit bell profiles; never retime slots belonging to a saved run."""

from datetime import time

from django.core.exceptions import ValidationError
from django.db import transaction

from ..bell_profiles import PROFILES, SOURCE_URL
from ..models import TimeSlot, WeekPattern


def bell_metadata(code):
    try:
        profile = PROFILES[code]
    except KeyError as exc:
        raise ValidationError("Неизвестный профиль звонков.") from exc
    return {"code": code, "scope": profile["scope"], "source_url": SOURCE_URL,
            "verified_on": "2026-10-24", "study_forms": profile["study_forms"]}


@transaction.atomic
def create_bell_pattern(*, semester, code, name=None, days=6):
    metadata = bell_metadata(code)
    if days not in (6, 7):
        raise ValidationError("Сетка должна содержать шесть или семь дней.")
    if days == 7:
        metadata["days"] = days
    pattern = WeekPattern.objects.create(
        semester=semester, name=name or code, bell_schedule=metadata)
    TimeSlot.objects.bulk_create([
        TimeSlot(week_pattern=pattern, day_of_week=day, lesson_number=number,
                 start_time=time.fromisoformat(start), end_time=time.fromisoformat(end))
        for day in range(days) for number, start, end in PROFILES[code]["times"]
    ])
    return pattern


def validate_bell_pattern(pattern, slots, events):
    """Manual historical patterns remain readable; a declared profile is strict."""
    metadata = pattern.bell_schedule
    if not metadata:
        return
    code = metadata.get("code")
    bell_metadata(code)
    expected = {(day, number, time.fromisoformat(start), time.fromisoformat(end))
                for day in range(metadata.get("days", 6)) for number, start, end in PROFILES[code]["times"]}
    actual = {(slot.day_of_week, slot.lesson_number, slot.start_time, slot.end_time)
              for slot in slots}
    if actual != expected:
        raise ValidationError("Слоты не соответствуют выбранному профилю звонков; создайте новый шаблон.")
    forms = set(events.exclude(curriculum_entry__isnull=True).values_list(
        "curriculum_entry__curriculum__study_form", flat=True))
    if forms - set(PROFILES[code]["study_forms"]):
        raise ValidationError("Форма обучения нагрузки не соответствует профилю звонков.")
