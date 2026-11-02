"""Independent interval/hour audit of saved results; never invokes the solver."""

from collections import Counter
from itertools import combinations

from django.db.models import Q

from schedule_generator.enums import Format, LessonType
from schedule_generator.models import LessonEvent, Student


def _minutes(value):
    if isinstance(value, str):
        hours, minutes = map(int, value.split(":")[:2])
        return hours * 60 + minutes
    return value.hour * 60 + value.minute


def _overlap(first, second, buffer=0):
    return max(first["start"] - buffer, second["start"]) < min(
        first["end"] + buffer, second["end"],
    )


def _same_occurrence(first, second):
    if first["day"] != second["day"]:
        return False
    dates_a, dates_b = first.get("dates"), second.get("dates")
    return dates_a is None or dates_b is None or bool(dates_a & dates_b)


def audit_run(run):
    """Check actual saved minutes, memberships and prepared hours, separately from CP-SAT.

    Ordinary memberships still come from current database relationships; this is
    not an immutable historical auditor. Fixed-PD participants come from the run
    snapshot. Routes and unselected PE offers are deliberately not inferred.
    """
    classes = list(run.scheduled_classes.select_related(
        "event__project__supervisor", "event__module", "event__stream", "slot",
    ).prefetch_related("event__stream__groups", "event__project__enrollments"))
    rows, missing_times = [], []
    student_cache = {}
    for item in classes:
        event = item.event
        groups = ([event.group_id] if event.group_id else
                  list(event.stream.groups.values_list("pk", flat=True)) if event.stream_id else [])
        if groups:
            key = tuple(sorted(groups))
            if key not in student_cache:
                student_cache[key] = set(Student.objects.filter(group_id__in=groups).values_list("pk", flat=True))
            students = student_cache[key]
        elif event.project_id:
            students = set(event.project.enrollments.values_list("student_id", flat=True))
        else:
            students = set()
        teacher_id = event.teacher_id or (event.project.supervisor_id if event.project_id else None)
        if item.slot.start_time is None or item.slot.end_time is None:
            missing_times.append(item.pk)
            continue
        rows.append({
            "id": f"class:{item.pk}", "event_id": event.pk, "kind": "class",
            "day": item.slot.day_of_week, "start": _minutes(item.slot.start_time),
            "end": _minutes(item.slot.end_time), "student_ids": students,
            "group_ids": set(groups), "teacher_id": teacher_id, "room_id": item.room_id,
            "onsite": event.effective_format == Format.ONSITE,
        })
    for fixed in run.fixed_pd_snapshot:
        dates = fixed.get("occurrence_dates")
        # An explicit dated session with no retained occurrences has no meetings.
        if dates == [] and fixed.get("date"):
            continue
        rows.append({
            "id": f"pd:{fixed['session_id']}", "event_id": None, "kind": "pd",
            "day": fixed["day_of_week"], "start": _minutes(fixed["start"]),
            "end": _minutes(fixed["end"]), "student_ids": set(fixed["student_ids"]),
            "group_ids": set(), "teacher_id": fixed["teacher_id"], "room_id": fixed["room_id"],
            "onsite": True, "buffer": fixed.get("travel_buffer_minutes", 0),
            "dates": set(dates) if dates is not None else None,
        })
    conflicts = {"students": [], "groups": [], "teachers": [], "rooms": [], "pd_buffer": []}
    for first, second in combinations(rows, 2):
        if not _same_occurrence(first, second):
            continue
        student_ids = first["student_ids"] & second["student_ids"]
        group_ids = first["group_ids"] & second["group_ids"]
        same_teacher = first["teacher_id"] is not None and first["teacher_id"] == second["teacher_id"]
        identity = [first["id"], second["id"]]
        if _overlap(first, second):
            if student_ids:
                conflicts["students"].append({"pairs": identity, "student_ids": sorted(student_ids)})
            if group_ids:
                conflicts["groups"].append({"pairs": identity, "group_ids": sorted(group_ids)})
            if same_teacher:
                conflicts["teachers"].append({"pairs": identity, "teacher_id": first["teacher_id"]})
            if first["room_id"] is not None and first["room_id"] == second["room_id"]:
                conflicts["rooms"].append({"pairs": identity, "room_id": first["room_id"]})
        if (first["kind"] == "pd") != (second["kind"] == "pd") and (student_ids or same_teacher):
            fixed, other = (first, second) if first["kind"] == "pd" else (second, first)
            same_room = fixed["room_id"] is not None and fixed["room_id"] == other["room_id"]
            if other["onsite"] and not same_room and _overlap(fixed, other, fixed["buffer"]):
                conflicts["pd_buffer"].append({"pairs": identity, "buffer_minutes": fixed["buffer"]})
    events = LessonEvent.objects.filter(semester=run.semester).filter(
        Q(module=run.module) | Q(module__isnull=True),
    ).exclude(lesson_type__in=[LessonType.PD, LessonType.PE]).select_related("module", "subject")
    event_counts = Counter(item.event_id for item in classes)
    loads, unmeasured = [], []
    for event in events:
        if event.academic_hours is None:
            unmeasured.append(event.pk)
            continue
        source = event.preparation_source or {}
        weeks = source.get("teaching_weeks")
        hours_per_pair = source.get("academic_hours_per_pair")
        if not weeks or not hours_per_pair:
            unmeasured.append(event.pk)
            continue
        loads.append({
            "event_id": event.pk, "subject": event.subject.name, "lesson_type": event.lesson_type,
            "required_hours": event.academic_hours,
            "placed_hours": event_counts[event.pk] * hours_per_pair * weeks,
            "required_pairs": event.pairs_per_week, "placed_pairs": event_counts[event.pk],
        })
    mismatches = [row for row in loads if row["required_hours"] != row["placed_hours"]
                  or row["required_pairs"] != row["placed_pairs"]]
    issues = list(run.issues.values("level", "text"))
    counts = {key: len(items) for key, items in conflicts.items()}
    return {
        "run_id": run.pk, "regular_pairs": sum(item.event.lesson_type != LessonType.PD for item in classes),
        "required_hours": sum(row["required_hours"] for row in loads),
        "placed_hours": sum(row["placed_hours"] for row in loads),
        "fixed_pd_count": len(run.fixed_pd_snapshot), "loads": loads,
        "hour_mismatches": mismatches, "conflicts": counts, "conflict_details": conflicts,
        "missing_slot_times": missing_times, "unmeasured_events": unmeasured, "issues": issues,
        "passed": not (mismatches or any(counts.values()) or missing_times or unmeasured or issues),
        "limitations": [
            "Проверяется подготовленная контактная нагрузка выбранного недельного шаблона, не полнота всего учебного плана.",
            "Маршруты между корпусами не рассчитываются; проверяется только явно заданный буфер фиксированной ПД.",
            "Физкультура не включается автоматически. Состав обычных занятий читается из текущих связей БД.",
        ],
    }
