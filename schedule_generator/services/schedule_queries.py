"""Выборки готового расписания, общие для API и сервисов (без HTTP).

Здесь живут правила «чьи пары считать»: какой запуск показывать и какие пары
относятся к группе или студенту. Их использует просмотр расписания, поиск
общих окон и свободных аудиторий, чтобы ответы совпадали между собой.
"""

from __future__ import annotations

from django.db.models import Q

from ..models import GenerationRun, ScheduledClass

SUCCESSFUL_STATUSES = ("SOLVED", "PARTIAL")


def resolve_run(semester, module=None, run_id=None):
    """Запуск, чьи пары показывать: явный ``run_id`` или последний SOLVED/PARTIAL
    запуск периода (семестр + модуль либо семестр без модуля)."""
    runs = GenerationRun.objects.filter(semester=semester)
    runs = runs.filter(module=module) if module is not None else runs.filter(module__isnull=True)
    if run_id is not None:
        return runs.filter(pk=run_id).first()
    return runs.filter(status__in=SUCCESSFUL_STATUSES).order_by("-created_at").first()


def scheduled_classes_for(semester, holder_filter, module=None, run_id=None,
                          week_pattern_id=None):
    """Пары выбранного запуска периода, отфильтрованные по носителю."""
    run = resolve_run(semester, module, run_id)
    if run is None:
        return ScheduledClass.objects.none()
    classes = (ScheduledClass.objects
               .filter(run=run)
               .filter(holder_filter)
               .select_related("slot", "room", "event", "event__subject",
                               "event__group", "event__stream", "event__project",
                               "event__teacher")
               .distinct())
    if week_pattern_id is not None:
        classes = classes.filter(slot__week_pattern_id=week_pattern_id)
    return classes


def group_holder_filter(group_id: int) -> Q:
    """Пары группы: её занятия и лекции потоков, в которые она входит."""
    return Q(event__group_id=group_id) | Q(event__stream__groups=group_id)


def student_holder_filter(student) -> Q:
    """Пары студента: пары его группы, лекции её потоков и ПД по выбранному проекту."""
    holder_filter = group_holder_filter(student.group_id)
    project_choice = getattr(student, "project_choice", None)
    if project_choice is not None:
        holder_filter |= Q(event__project_id=project_choice.project_id)
    return holder_filter
