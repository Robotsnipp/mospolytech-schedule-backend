"""Запись на физкультуру: проверка правил выбора слота."""

from __future__ import annotations

from datetime import date

from django.db.models import Q

from ..models import PEEnrollment


class PEEnrollmentError(Exception):
    """Слот физкультуры нельзя выбрать (нарушено правило)."""


def check_can_enroll(owner: str, group_title: str, slot) -> None:
    """Не больше одного слота в день и без пересечения с парами группы."""
    from rasp.models import Lesson, PDSlot
    from rasp.pair_times import PAIR_TIMES

    if PEEnrollment.objects.filter(owner=owner, slot=slot).exists():
        raise PEEnrollmentError("Вы уже записаны на этот слот.")
    if PEEnrollment.objects.filter(owner=owner, slot__day_of_week=slot.day_of_week).exists():
        raise PEEnrollmentError("В этот день уже выбрана физкультура: не более одной в день.")
    for pd_slot in PDSlot.objects.filter(project__enrollments__owner=owner,
                                         day_of_week=slot.day_of_week):
        if slot.start_time < pd_slot.end_time and pd_slot.start_time < slot.end_time:
            raise PEEnrollmentError(
                f"Конфликт с проектной деятельностью ({pd_slot.start_time:%H:%M}–{pd_slot.end_time:%H:%M}).")
    if not group_title:
        return

    today = date.today()
    current = Q(date_to__isnull=True) | Q(date_to__gte=today)
    lessons = (Lesson.objects.filter(current, group__title=group_title, is_session=False,
                                     weekday=slot.day_of_week + 1).select_related("subject"))
    for item in lessons:
        start, end = PAIR_TIMES.get(item.pair, (None, None))
        if start and slot.start_time < end and start < slot.end_time:
            raise PEEnrollmentError(
                f"Конфликт с парой «{item.subject.name}» ({start:%H:%M}–{end:%H:%M}).")
