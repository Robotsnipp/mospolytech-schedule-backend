"""Выбор проекта: загрузка таблицы в БД и проверка конфликтов."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from django.db import transaction
from django.db.models import Count, Q

from .models import Lesson, PDEnrollment, PDProject, PDSlot
from .pair_times import PAIR_TIMES
from .pd_parser import parse_csv


class PDEnrollmentError(Exception):
    """Проект нельзя выбрать (конфликт с расписанием)."""


@transaction.atomic
def store_projects(text: str) -> dict:
    """Идемпотентно заменяет набор проектов; исчезнувшие удаляются вместе с выбором."""
    parsed = parse_csv(text)
    keep = []
    slots = 0
    for item in parsed:
        project, _ = PDProject.objects.update_or_create(number=item.number, defaults={
            "direction": item.direction, "title": item.title, "teacher": item.teacher,
            "contact": item.contact, "note": item.note, "irregular": item.irregular})
        project.slots.all().delete()
        PDSlot.objects.bulk_create([
            PDSlot(project=project, day_of_week=s.day_of_week, start_time=s.start,
                   end_time=s.end, room=s.room) for s in item.slots])
        slots += len(item.slots)
        keep.append(project.id)
    removed = PDProject.objects.exclude(id__in=keep).delete()[1].get("rasp.PDProject", 0)
    return {"projects": len(parsed), "slots": slots, "removed": removed,
            "directions": PDProject.objects.values("direction").distinct().count()}


def load_file(path: Path) -> dict:
    return store_projects(path.read_text(encoding="utf-8"))


def directions() -> list[dict]:
    rows = PDProject.objects.values("direction").annotate(count=Count("id")).order_by("direction")
    return [{"name": r["direction"], "count": r["count"]} for r in rows]


def check_can_enroll(owner: str, group_title: str, project: PDProject) -> None:
    """Слоты проекта не должны пересекаться с парами группы и выбранной физкультурой."""
    from schedule_generator.models import PEEnrollment

    today = date.today()
    current = Q(date_to__isnull=True) | Q(date_to__gte=today)
    lessons = []
    if group_title:
        # «Проектная деятельность» в расписании группы — заглушка (место ПД), её заменяет выбор.
        lessons = list(Lesson.objects.filter(current, group__title=group_title, is_session=False)
                       .exclude(location="ПД").select_related("subject"))
    pe = list(PEEnrollment.objects.filter(owner=owner).select_related("slot", "slot__direction"))
    for slot in project.slots.all():
        for item in lessons:
            start, end = PAIR_TIMES.get(item.pair, (None, None))
            if (item.weekday - 1 == slot.day_of_week and start
                    and slot.start_time < end and start < slot.end_time):
                raise PDEnrollmentError(
                    f"Конфликт с парой «{item.subject.name}» ({start:%H:%M}–{end:%H:%M}).")
        for enrollment in pe:
            other = enrollment.slot
            if (other.day_of_week == slot.day_of_week
                    and slot.start_time < other.end_time and other.start_time < slot.end_time):
                raise PDEnrollmentError(
                    f"Конфликт с физкультурой ({other.start_time:%H:%M}–{other.end_time:%H:%M}).")


@transaction.atomic
def enroll(owner: str, group_title: str, project: PDProject) -> PDEnrollment:
    """Записывает на проект; прежний выбор владельца заменяется."""
    check_can_enroll(owner, group_title, project)
    PDEnrollment.objects.filter(owner=owner).delete()
    return PDEnrollment.objects.create(owner=owner, group_title=group_title, project=project)
