"""Freeze inputs used by the solver and every subsequent manual validation."""
from dataclasses import asdict

from ..models import Student, Teacher
from .config import TRAVEL_BUFFER_SLOTS
from .settings import minute


def build_snapshot(*, group, settings, slots, specs, events, rooms, fixed_pd, semester, module, week_pattern):
    event_by_id = {event.pk: event for event in events.select_related('subject', 'teacher', 'stream', 'group')}
    snapshot_events = []
    for spec in specs:
        event = event_by_id[spec.event_id]
        snapshot_events.append({
            'id': spec.event_id, 'event_id': spec.event_id, 'subject_id': spec.subject_id,
            'subject_name': event.subject.name, 'name': event.subject.name,
            'lesson_type': spec.lesson_type, 'format': spec.format,
            'pairs_per_week': spec.pairs_per_week, 'audience_size': spec.audience_size,
            'module_id': event.module_id, 'academic_hours': event.academic_hours,
            'academic_hours_per_pair': event.preparation_source.get('academic_hours_per_pair'),
            'teacher_id': spec.teacher_id, 'teacher_name': event.teacher.full_name if event.teacher_id else '',
            'student_ids': sorted(spec.attending_student_ids),
            'blocking_group_ids': sorted(spec.blocking_group_ids),
            'blocking_student_ids': sorted(spec.blocking_student_ids),
            'participant_group_ids': sorted(spec.participant_group_ids),
            'allowed_days': sorted(spec.allowed_days),
            'group_id': event.group_id, 'group_name': event.group.name if event.group_id else '',
            'stream_id': event.stream_id, 'stream_name': str(event.stream) if event.stream_id else '',
        })
    fixed = []
    for item in fixed_pd:
        data = asdict(item)
        for key in ('student_ids', 'participant_group_ids'):
            data[key] = sorted(data[key])
        data['occurrence_dates'] = [day.isoformat() for day in sorted(item.occurrence_dates)]
        fixed.append(data)
    return {'schema_version': 1, 'settings': settings, 'group_id': group.pk, 'group_name': group.name,
            'semester_id': semester.pk, 'module_id': module.pk if module else None,
            'module_teaching_weeks': module.teaching_weeks if module else None,
            'module_number': module.number if module else None,
            'module_start_date': module.start_date.isoformat() if module else None,
            'module_end_date': module.end_date.isoformat() if module else None,
            'week_pattern_id': week_pattern.pk, 'travel_buffer_slots': TRAVEL_BUFFER_SLOTS,
            'students': list(Student.objects.filter(group=group).values('id', 'group_id')),
            'events': snapshot_events,
            'slots': [{'id': slot.pk, 'day_of_week': slot.day_of_week, 'lesson_number': slot.lesson_number,
                       'start_minute': minute(slot.start_time), 'end_minute': minute(slot.end_time),
                       'start_time': slot.start_time.strftime('%H:%M'), 'end_time': slot.end_time.strftime('%H:%M')}
                      for slot in slots],
            'rooms': rooms, 'fixed_pd': fixed,
            'teachers': list(Teacher.objects.filter(pk__in={spec.teacher_id for spec in specs}).values(
                'id', 'full_name', 'max_pairs_per_day', 'chronotype'))}
