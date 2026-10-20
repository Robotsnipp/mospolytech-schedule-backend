"""Запись студента на физкультуру: проверка правил выбора слота."""

from __future__ import annotations

from ..models import PEEnrollment
from .schedule_queries import scheduled_classes_for, student_holder_filter


class PEEnrollmentError(Exception):
    """Слот физкультуры нельзя выбрать (нарушено правило)."""


def check_can_enroll(student, slot) -> None:
    """Не больше одного слота в день и без пересечения с парами студента."""
    if PEEnrollment.objects.filter(student=student, slot=slot).exists():
        raise PEEnrollmentError("Вы уже записаны на этот слот.")
    if PEEnrollment.objects.filter(student=student,
                                   slot__day_of_week=slot.day_of_week).exists():
        raise PEEnrollmentError("В этот день уже выбрана физкультура: не более одной в день.")

    semester = student.group.semester
    if semester is None:
        return
    classes = scheduled_classes_for(semester, student_holder_filter(student))
    for item in classes:
        start, end = item.slot.start_time, item.slot.end_time
        if item.slot.day_of_week != slot.day_of_week or start is None or end is None:
            continue
        if slot.start_time < end and start < slot.end_time:
            raise PEEnrollmentError(
                f"Конфликт с парой «{item.event.subject.name}» "
                f"({start:%H:%M}–{end:%H:%M}).")
