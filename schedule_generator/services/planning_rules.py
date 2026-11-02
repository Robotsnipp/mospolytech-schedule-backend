"""Concrete personal-rule semantics shared by generation and manual variants.

Pair counts use distinct intersected bell numbers; gaps retain literal minutes.
"""
from collections import defaultdict

from .settings import DAYS, minute

RULE_LABELS = {
    'free_days': 'Запланированы занятия в свободный день',
    'earliest_start': 'Занятия начинаются раньше выбранной границы',
    'latest_end': 'Занятия заканчиваются позже выбранной границы',
    'max_pairs_per_day': 'Превышен максимум занятых пар за день',
    'min_pairs_per_day': 'Недостаточно занятых пар в учебный день',
    'max_break_minutes': 'Слишком длинный отдельный перерыв между занятиями',
    'max_consecutive_pairs': 'Превышен максимум пар подряд',
    'max_study_days': 'Превышен максимум учебных дней',
    'max_building_changes': 'Превышен максимум смен корпуса за день',
    'preferred_building': 'Очная пара находится вне выбранного корпуса',
}
DAY_LABELS = ('Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье')


def occupied_numbers(day, start, end, slots):
    return {slot['lesson_number'] for slot in slots if slot['day_of_week'] == day
            and start < slot['end_minute'] and slot['start_minute'] < end}


def attendance_profiles(snapshot):
    """Group identical attendance signatures without losing affected people."""
    grouped = defaultdict(list)
    for student in snapshot['students']:
        sid = student['id']
        events = tuple(event['id'] for event in snapshot['events'] if sid in event['student_ids'])
        fixed = tuple(index for index, item in enumerate(snapshot['fixed_pd']) if sid in item['student_ids'])
        grouped[(events, fixed)].append(sid)
    return [{'event_ids': events, 'fixed_indices': fixed, 'student_ids': students, 'weight': len(students)}
            for (events, fixed), students in grouped.items()]


def longest_series(numbers):
    best = current = 0
    previous = None
    for number in sorted(numbers):
        current = current + 1 if previous == number - 1 else 1
        best = max(best, current)
        previous = number
    return best


def validate_rules(snapshot, placements):
    settings = snapshot.get('settings', {})
    rules = settings.get('rules', {})
    result = {'blocking': [], 'warnings': [], 'incomplete': [], 'metrics': {}}
    slots = {slot['id']: slot for slot in snapshot['slots']}
    rooms = {room['id']: room for room in snapshot['rooms']}
    events = {event['id']: event for event in snapshot['events']}
    profiles = attendance_profiles(snapshot)

    def issue(code, rule, actual, limit, profile, day=None, ids=None, reason=None):
        message = reason or RULE_LABELS.get(code, 'Нарушено условие расписания')
        if day is not None:
            message = f'{DAY_LABELS[day]}: {message}'
        item = {'code': code, 'mode': rule['mode'], 'actual': actual, 'limit': limit,
                'student_ids': profile['student_ids'], 'day_of_week': day,
                'event_ids': sorted(set(ids or [])), 'message': message}
        result['blocking' if rule['mode'] == 'required' else 'warnings'].append(item)

    def incomplete(code, profile, day, reason):
        result['incomplete'].append({'code': code, 'student_ids': profile['student_ids'],
                                     'day_of_week': day, 'reason': reason, 'message': reason})

    for index, profile in enumerate(profiles):
        by_day = defaultdict(list)
        for placement in placements:
            event = events.get(placement['event_id'])
            slot = slots.get(placement['slot_id'])
            if event is None or slot is None or event['id'] not in profile['event_ids']:
                continue
            room = rooms.get(placement.get('room_id'))
            by_day[slot['day_of_week']].append({
                'start': slot['start_minute'], 'end': slot['end_minute'],
                'numbers': {slot['lesson_number']}, 'event_id': event['id'],
                'onsite': event['format'] == 'ONSITE',
                'building': f"building:{room['building_id']}" if room and room.get('building_id') else None,
            })
        for fixed_index in profile['fixed_indices']:
            fixed = snapshot['fixed_pd'][fixed_index]
            room = rooms.get(fixed.get('room_id'))
            building = f"building:{room['building_id']}" if room and room.get('building_id') else (
                f"external:{fixed['place_raw']}" if fixed.get('place_raw') else None)
            numbers = occupied_numbers(fixed['day_of_week'], fixed['start_minute'], fixed['end_minute'], snapshot['slots'])
            if not numbers and any(key in rules for key in ('max_pairs_per_day', 'min_pairs_per_day', 'max_consecutive_pairs')):
                active = [key for key in ('max_pairs_per_day', 'min_pairs_per_day', 'max_consecutive_pairs') if key in rules]
                for key in active:
                    if rules[key]['mode'] == 'required':
                        issue(key, rules[key], None, rules[key]['value'], profile, fixed['day_of_week'],
                              reason='Фиксированная ПД вне сетки: обязательный подсчёт занятых пар неполон.')
                    else:
                        incomplete(key, profile, fixed['day_of_week'], 'ПД вне сетки звонков; подсчёт занятых пар неполон.')
            by_day[fixed['day_of_week']].append({'start': fixed['start_minute'], 'end': fixed['end_minute'],
                'numbers': numbers, 'event_id': None, 'onsite': True, 'building': building})
        metrics = {'student_ids': profile['student_ids'], 'study_days': len(by_day), 'days': []}
        for day, timeline in sorted(by_day.items()):
            timeline.sort(key=lambda item: (item['start'], item['end']))
            numbers = set().union(*(item['numbers'] for item in timeline))
            gaps = [max(0, right['start'] - left['end']) for left, right in zip(timeline, timeline[1:], strict=False)]
            onsite = [item for item in timeline if item['onsite']]
            changes = sum(bool(left['building'] and right['building'] and left['building'] != right['building'])
                          for left, right in zip(onsite, onsite[1:], strict=False))
            unknown_buildings = any(item['building'] is None for item in onsite)
            if unknown_buildings and 'max_building_changes' in rules:
                incomplete('max_building_changes', profile, day, 'Неизвестно место очного занятия; число смен корпуса проверено частично.')
            ids = [item['event_id'] for item in timeline if item['event_id'] is not None]
            values = {'max_pairs_per_day': len(numbers), 'max_consecutive_pairs': longest_series(numbers),
                      'min_pairs_per_day': len(numbers), 'max_building_changes': changes,
                      'earliest_start': timeline[0]['start'], 'latest_end': max(item['end'] for item in timeline)}
            metrics['days'].append({'day_of_week': day, 'occupied_pairs': sorted(numbers),
                                   'break_minutes': gaps, 'building_changes': changes})
            for free_day in rules.get('free_days', []):
                if DAYS[day] == free_day['day']:
                    issue('free_days', free_day, len(timeline), 0, profile, day, ids)
            for key, actual in values.items():
                rule = rules.get(key)
                if not rule:
                    continue
                limit = minute(rule['value']) if key in ('earliest_start', 'latest_end') else rule['value']
                violated = actual < limit if key in ('min_pairs_per_day', 'earliest_start') else actual > limit
                if violated:
                    issue(key, rule, actual, limit, profile, day, ids)
            if 'max_break_minutes' in rules:
                rule = rules['max_break_minutes']
                for gap in gaps:
                    if gap > rule['value']:
                        issue('max_break_minutes', rule, gap, rule['value'], profile, day, ids)
        if 'max_study_days' in rules and len(by_day) > rules['max_study_days']['value']:
            issue('max_study_days', rules['max_study_days'], len(by_day), rules['max_study_days']['value'], profile)
        result['metrics'][str(index)] = metrics
    rule = rules.get('preferred_building')
    if rule:
        profile = {'student_ids': [student['id'] for student in snapshot['students']]}
        for placement in placements:
            event = events.get(placement['event_id'])
            if not event or event['format'] != 'ONSITE':
                continue
            room = rooms.get(placement.get('room_id'))
            if not room or not room.get('building_id'):
                incomplete('preferred_building', profile, slots[placement['slot_id']]['day_of_week'],
                           'Аудитория не назначена; требование корпуса пока не проверено.')
            elif room['building_id'] != rule['value']:
                issue('preferred_building', rule, room['building_id'], rule['value'], profile,
                      slots[placement['slot_id']]['day_of_week'], [event['id']])
    return result
