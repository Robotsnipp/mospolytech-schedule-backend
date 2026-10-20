"""Проекции одного сохранённого запуска: пары и снимок фиксированной ПД."""

from copy import deepcopy
from datetime import date

from django.core.exceptions import ValidationError
from django.db.models import Q

from ..enums import Format, LessonType
from ..models import GenerationRun, Room, Student, Teacher


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
        run = (period_runs.filter(module=module, status__in=["SOLVED", "PARTIAL"])
               .order_by("-created_at", "-pk").first())
    if week_pattern is not None and week_pattern.semester_id != semester.pk:
        raise ValidationError({"week_pattern": "Шаблон недели относится к другому семестру."})
    return run


def _holder_filter(kind, holder):
    if kind == "student":
        return (Q(event__group_id=holder.group_id)
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
        "slot__week_pattern__semester", "room__building__location", "event__subject", "event__group",
        "event__stream__subject", "event__project__supervisor", "event__teacher")
        .prefetch_related("event__stream__groups", "event__project__enrollments__student"))


def read_schedule(*, run, kind="all", holder=None, week_pattern=None):
    if run is None:
        return {"run": None, "scheduled_classes": [], "fixed_pd_snapshot": [], "issues": []}
    classes = (with_schedule_details(run.scheduled_classes.filter(_holder_filter(kind, holder)))
               .distinct().order_by("slot__day_of_week", "slot__lesson_number", "pk"))
    if week_pattern is not None:
        classes = classes.filter(slot__week_pattern=week_pattern)
    issues = (run.issues.filter(Q(event__isnull=True) | _holder_filter(kind, holder))
              .select_related("event").distinct().order_by("pk"))
    return {"run": run, "scheduled_classes": classes, "issues": issues,
            "fixed_pd_snapshot": _fixed_pd_for(run, kind, holder, week_pattern)}
