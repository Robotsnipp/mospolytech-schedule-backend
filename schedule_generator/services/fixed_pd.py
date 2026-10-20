"""Проекция фиксированных занятий ПД в выбранный недельный шаблон."""

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db.models import Q

from ..models import FixedProjectSession
from .dto import FixedPDSpec


def minute(value):
    if value is None or value.second or value.microsecond:
        raise ValueError("Для учёта ПД нужны начало и конец каждого слота с точностью до минуты.")
    return value.hour * 60 + value.minute


def occurrence_dates(session, semester, module, week_pattern):
    start = semester.start_date
    end = start + timedelta(weeks=semester.weeks_count) - timedelta(days=1)
    if module:
        start, end = max(start, module.start_date), min(end, module.end_date)
    dates = set()
    for offset in range(max(0, (end - start).days + 1)):
        day = start + timedelta(days=offset)
        first_monday = semester.start_date - timedelta(days=semester.start_date.weekday())
        week = (day - first_monday).days // 7 + 1
        if (day.weekday() == session.day_of_week and (session.date is None or session.date == day)
                and (week_pattern.parity == 0 or week % 2 == week_pattern.parity % 2)):
            dates.add(day)
    return frozenset(dates)


def load_fixed_pd(*, semester, module, week_pattern, slots):
    sessions = FixedProjectSession.objects.filter(project__semester=semester).select_related(
        "project__semester", "project__supervisor", "module", "teacher", "room__building__location"
    ).prefetch_related("project__enrollments__student")
    if module:
        sessions = sessions.filter(Q(module=module) | Q(module__isnull=True))
    specs, snapshot = [], []
    for session in sessions.order_by("pk"):
        session.full_clean()
        dates = occurrence_dates(session, semester, module, week_pattern)
        if not dates:
            continue
        participants = [e.student for e in session.project.enrollments.all()]
        if not participants:
            raise ValidationError(f"ПД #{session.pk}: нет связей студент → проект; пересечения проверить нельзя.")
        teacher_id = session.teacher_id or session.project.supervisor_id
        if teacher_id is None:
            raise ValidationError(f"ПД #{session.pk}: не сопоставлен преподаватель.")
        if session.room_id and not session.room.is_active:
            raise ValidationError(f"ПД #{session.pk}: аудитория недоступна.")
        spec = FixedPDSpec(
            session_id=session.pk, project_id=session.project_id,
            day_of_week=session.day_of_week, start_minute=minute(session.start_time),
            end_minute=minute(session.end_time), teacher_id=teacher_id,
            student_ids=frozenset(s.pk for s in participants),
            participant_group_ids=frozenset(s.group_id for s in participants),
            room_id=session.room_id, place_raw=session.place_details.get("raw") or "",
            travel_buffer_minutes=session.travel_buffer_minutes, occurrence_dates=dates,
        )
        specs.append(spec)
        snapshot.append({
            "session_id": session.pk, "project_id": session.project_id,
            "project_title": session.project.title, "day_of_week": spec.day_of_week,
            "start": session.start_time.strftime("%H:%M"), "end": session.end_time.strftime("%H:%M"),
            "date": session.date.isoformat() if session.date else None,
            "occurrence_dates": [d.isoformat() for d in sorted(dates)],
            "teacher_id": teacher_id, "student_ids": sorted(spec.student_ids),
            "student_group_ids": {str(s.pk): s.group_id for s in participants},
            "teacher_name": (session.teacher or session.project.supervisor).full_name,
            "room_name": session.room.name if session.room_id else None,
            "location_name": session.room.location_name_for_ui() if session.room_id else "",
            "participant_group_ids": sorted(spec.participant_group_ids), "room_id": spec.room_id,
            "travel_buffer_minutes": spec.travel_buffer_minutes,
            "place_details": session.place_details, "source": session.source,
        })
    if specs:
        for slot in slots:
            if minute(slot.start_time) >= minute(slot.end_time):
                raise ValidationError(f"Слот #{slot.pk}: начало должно быть раньше конца.")
    validate_fixed_conflicts(specs)
    return specs, snapshot


def validate_fixed_conflicts(specs):
    for i, first in enumerate(specs):
        for second in specs[i + 1:]:
            if not first.occurrence_dates & second.occurrence_dates:
                continue
            overlap = first.start_minute < second.end_minute and second.start_minute < first.end_minute
            shared_room = first.room_id is not None and first.room_id == second.room_id
            shared_people = (first.teacher_id == second.teacher_id
                             or bool(first.student_ids & second.student_ids))
            same_place = shared_room or bool(first.place_raw and first.place_raw == second.place_raw)
            buffer = 0 if same_place else max(first.travel_buffer_minutes, second.travel_buffer_minutes)
            travel_conflict = (first.start_minute < second.end_minute + buffer
                               and second.start_minute < first.end_minute + buffer)
            if (overlap and shared_room) or (shared_people and travel_conflict):
                raise ValidationError(f"Конфликт фиксированных занятий ПД #{first.session_id} и #{second.session_id}: "
                                      "участники, преподаватель, аудитория или запас на дорогу.")
