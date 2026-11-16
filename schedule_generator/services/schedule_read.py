"""Проекции сохранённого запуска: пары и снимок фиксированной ПД.

Расписание студента, преподавателя и группы по умолчанию (без run_id)
объединяет основной запуск периода с запусками программ ДПО, где участвует
человек или группа ДПО (merge_dpo_runs).
"""

from copy import deepcopy
from datetime import date

from django.core.exceptions import ValidationError
from django.db.models import Q

from ..enums import Format, LessonType
from ..models import (
    GenerationIssue,
    GenerationRun,
    Room,
    ScheduledClass,
    Student,
    Teacher,
)


def resolve_schedule_run(*, semester=None, module=None, run_id=None, week_pattern=None, **_):
    runs = GenerationRun.objects.select_related("semester", "module")
    if run_id is not None:
        run = runs.get(pk=run_id)
        if semester is not None and run.semester_id != semester.pk:
            raise ValidationError({"semester": "Запуск относится к другому семестру."})
        if module is not None and run.module_id != module.pk:
            raise ValidationError({"module": "Запуск относится к другому модулю."})
        semester = run.semester
    else:
        if module is not None and module.semester_id != semester.pk:
            raise ValidationError({"module": "Модуль относится к другому семестру."})
        period_runs = runs.filter(semester=semester)
        if module is None and not period_runs.filter(module__isnull=True).exists() \
                and period_runs.filter(module__isnull=False).exists():
            raise ValidationError({"module": "Укажите модуль или run_id модульного расписания."})
        run = (period_runs.filter(module=module, status__in=["SOLVED", "PARTIAL"],
                                  dpo_program__isnull=True)
               .order_by("-created_at", "-pk").first())
    if week_pattern is not None and week_pattern.semester_id != semester.pk:
        raise ValidationError({"week_pattern": "Шаблон недели относится к другому семестру."})
    return run


def _holder_filter(kind, holder):
    if kind == "student":
        return (Q(event__group_id=holder.group_id)
                | Q(event__group__dpo_memberships__student=holder)
                | Q(event__stream__groups=holder.group_id)
                | Q(event__project__enrollments__student=holder))
    if kind == "group":
        return (Q(event__group=holder) | Q(event__stream__groups=holder)
                | Q(event__project__enrollments__student__group=holder))
    if kind == "teacher":
        return (Q(event__teacher=holder)
                | Q(event__teacher__isnull=True, event__project__supervisor=holder))
    if kind == "stream":
        return Q(event__stream=holder)
    return Q()


def _pattern_dates(session, semester, pattern):
    """Фильтрует только сохранённые даты; исходник ПД заново не читается."""
    dates = session.get("occurrence_dates", [])
    if not pattern.parity:
        return dates
    first_monday = semester.start_date.toordinal() - semester.start_date.weekday()
    return [value for value in dates
            if ((date.fromisoformat(value).toordinal() - first_monday) // 7 + 1) % 2
            == pattern.parity % 2]


def _fixed_pd_for(run, kind, holder, week_pattern):
    if kind == "stream":
        return []
    sessions = []
    # Старые снимки содержат состав проекта, но не группу каждого участника.
    group_student_ids = None
    for original in run.fixed_pd_snapshot:
        session = deepcopy(original)
        if kind == "student" and holder.pk not in session["student_ids"]:
            continue
        if kind == "teacher" and session["teacher_id"] != holder.pk:
            continue
        if kind == "group":
            if holder.pk not in session["participant_group_ids"]:
                continue
            membership = session.get("student_group_ids")
            if membership is not None:
                relevant = {int(pk) for pk, group_id in membership.items() if group_id == holder.pk}
            else:
                if group_student_ids is None:
                    group_student_ids = set(Student.objects.filter(group=holder).values_list("pk", flat=True))
                relevant = group_student_ids
            session["student_ids"] = [pk for pk in session["student_ids"] if pk in relevant]
            session["participant_group_ids"] = [holder.pk]
        if kind == "student":
            session["student_ids"] = [holder.pk]
        if week_pattern is not None:
            session["occurrence_dates"] = _pattern_dates(session, run.semester, week_pattern)
            if not session["occurrence_dates"]:
                continue
        session.update(run=run.pk, lesson_type=LessonType.PD, format=Format.ONSITE)
        sessions.append(session)
    # Метки старых снимков дополняются справочниками; новые сохраняют их при генерации.
    teachers = Teacher.objects.in_bulk({s["teacher_id"] for s in sessions if "teacher_name" not in s})
    rooms = Room.objects.select_related("building__location").in_bulk(
        {s["room_id"] for s in sessions if s["room_id"] and "room_name" not in s})
    for session in sessions:
        teacher = teachers.get(session["teacher_id"])
        room = rooms.get(session["room_id"])
        session.setdefault("teacher_name", teacher.full_name if teacher else None)
        session.setdefault("room_name", room.name if room else None)
        session.setdefault("location_name", room.location_name_for_ui() if room else "")
    return sorted(sessions, key=lambda s: (s["day_of_week"], s["start"], s["session_id"]))


def with_schedule_details(classes):
    """Общий граф данных для ScheduledClassSerializer без запросов на каждую пару."""
    return (classes.select_related(
        "run", "slot__week_pattern__semester", "room__building__location", "event__subject", "event__group",
        "event__stream__subject", "event__project__supervisor", "event__teacher")
        .prefetch_related("event__stream__groups", "event__project__enrollments__student"))


def read_schedule(*, run, kind="all", holder=None, week_pattern=None):
    if run is None:
        return {"run": None, "scheduled_classes": [], "fixed_pd_snapshot": [], "issues": []}
    holder_filter = _holder_filter(kind, holder)
    if run.input_snapshot and holder is not None:
        events = run.input_snapshot.get("events", [])
        def belongs(event):
            if kind == "student":
                return holder.pk in event.get("student_ids", [])
            if kind == "group":
                return holder.pk in event.get("blocking_group_ids", []) or event.get("group_id") == holder.pk
            if kind == "teacher":
                return event.get("teacher_id") == holder.pk
            if kind == "stream":
                return event.get("stream_id") == holder.pk
            return True
        holder_filter = Q(event_id__in=[event["id"] for event in events if belongs(event)])
    classes = (with_schedule_details(run.scheduled_classes.filter(holder_filter))
               .distinct().order_by("slot__day_of_week", "slot__lesson_number", "pk"))
    if week_pattern is not None:
        classes = classes.filter(slot__week_pattern=week_pattern)
    issues = (run.issues.filter(Q(event__isnull=True) | holder_filter)
              .select_related("event").distinct().order_by("pk"))
    return {"run": run, "scheduled_classes": classes, "issues": issues,
            "fixed_pd_snapshot": _fixed_pd_for(run, kind, holder, week_pattern)}


def latest_dpo_runs(semester, module=None):
    """Последний успешный запуск каждой программы ДПО, чей период пересекается с
    периодом (семестр + модуль) основного расписания."""
    from .dpo import _period_bounds  # dpo.py импортирует генерацию: импорт по месту

    runs = (GenerationRun.objects
            .filter(semester=semester, status__in=["SOLVED", "PARTIAL"], dpo_program__isnull=False)
            .select_related("semester", "module").order_by("dpo_program_id", "-created_at", "-pk"))
    latest = {}
    for run in runs:
        latest.setdefault(run.dpo_program_id, run)
    start, end = _period_bounds(semester, module)
    result = []
    for run in latest.values():
        run_start, run_end = _period_bounds(semester, run.module)
        if run_start <= end and start <= run_end:
            result.append(run)
    return sorted(result, key=lambda run: run.pk)


def merge_dpo_runs(result, *, kind, holder, week_pattern=None, dpo_runs=()):
    """Добавляет к ответу пары и замечания запусков ДПО, относящиеся к человеку.

    ``run`` ответа остаётся основным запуском; номера добавленных запусков - в
    ``dpo_runs``, у каждой пары свой ``run``.
    """
    class_ids = [scheduled.pk for scheduled in result["scheduled_classes"]]
    issue_ids = [issue.pk for issue in result["issues"]]
    merged_runs = []
    for run in dpo_runs:
        extra = read_schedule(run=run, kind=kind, holder=holder, week_pattern=week_pattern)
        extra_ids = list(extra["scheduled_classes"].values_list("pk", flat=True))
        if not extra_ids:
            continue
        merged_runs.append(run.pk)
        class_ids += extra_ids
        issue_ids += list(extra["issues"].values_list("pk", flat=True))
    if not merged_runs:
        return {**result, "dpo_runs": []}
    classes = (with_schedule_details(ScheduledClass.objects.filter(pk__in=class_ids))
               .order_by("slot__day_of_week", "slot__lesson_number", "pk"))
    issues = GenerationIssue.objects.filter(pk__in=issue_ids).select_related("event").order_by("pk")
    return {**result, "scheduled_classes": classes, "issues": issues, "dpo_runs": merged_runs}
