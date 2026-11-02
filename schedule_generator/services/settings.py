"""Versioned finite settings contract for the single-group editor."""

from copy import deepcopy
from datetime import time

DAYS = ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun')
POLICY_VERSION = 'personal-settings-v1'
NUMERIC_RULES = {
    'max_pairs_per_day': (1, 16), 'max_break_minutes': (0, 1440),
    'max_consecutive_pairs': (1, 16), 'min_pairs_per_day': (1, 16),
    'max_study_days': (1, 7), 'max_building_changes': (0, 30),
    'preferred_building': (1, 2147483647),
}
RULE_KEYS = {*NUMERIC_RULES, 'free_days', 'earliest_start', 'latest_end'}
DEFAULT_SETTINGS = {
    'schema_version': 1,
    'rules': {'free_days': [{'day': 'sat', 'mode': 'preferred'}, {'day': 'sun', 'mode': 'required'}]},
    'search': {'time_limit_seconds': 30, 'seed': 17},
}


def _keys(value, allowed, label):
    if not isinstance(value, dict):
        raise ValueError(f'{label}: ожидается объект.')
    unknown = set(value) - set(allowed)
    if unknown:
        raise ValueError(f'{label}: неизвестные поля {", ".join(sorted(unknown))}.')


def _integer(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{label}: нужно целое число от {low} до {high}.')
    return value


def normalize_settings(value):
    _keys(value, {'schema_version', 'rules', 'search', 'policy_version'}, 'settings')
    if type(value.get('schema_version')) is not int or value['schema_version'] != 1:
        raise ValueError('Неподдерживаемая версия настроек; нужна schema_version=1.')
    if value.get('policy_version', POLICY_VERSION) != POLICY_VERSION:
        raise ValueError('Неподдерживаемая версия правил.')
    rules = value.get('rules', {})
    _keys(rules, RULE_KEYS, 'rules')
    normalized = {}
    for key, rule in rules.items():
        if key == 'free_days':
            if not isinstance(rule, list):
                raise ValueError('free_days: ожидается список дней.')
            days = []
            seen = set()
            for day in rule:
                _keys(day, {'day', 'mode'}, 'free_days')
                if day.get('day') not in DAYS or day.get('day') in seen:
                    raise ValueError('free_days: неизвестный или повторный день.')
                if day.get('mode') not in ('required', 'preferred'):
                    raise ValueError('У правила нужен режим required или preferred.')
                days.append(dict(day))
                seen.add(day['day'])
            normalized[key] = days
            continue
        _keys(rule, {'value', 'mode'}, key)
        if rule.get('mode') not in ('required', 'preferred'):
            raise ValueError(f'{key}: нужен режим required или preferred.')
        number = rule.get('value')
        if key in NUMERIC_RULES:
            number = _integer(number, *NUMERIC_RULES[key], key)
        else:
            if not isinstance(number, str) or len(number) != 5:
                raise ValueError(f'{key}: нужно время HH:MM.')
            try:
                parsed = time.fromisoformat(number)
            except ValueError as exc:
                raise ValueError(f'{key}: нужно время HH:MM.') from exc
            if parsed.second or parsed.microsecond:
                raise ValueError(f'{key}: точность времени — минуты.')
        normalized[key] = {'value': number, 'mode': rule['mode']}
    if ('earliest_start' in normalized and 'latest_end' in normalized
            and normalized['earliest_start']['value'] >= normalized['latest_end']['value']):
        raise ValueError('Начало занятий должно быть раньше окончания.')
    search = value.get('search', {})
    _keys(search, {'time_limit_seconds', 'seed'}, 'search')
    return {'schema_version': 1, 'policy_version': POLICY_VERSION, 'rules': normalized,
            'search': {'time_limit_seconds': _integer(search.get('time_limit_seconds', 30), 1, 600, 'time_limit_seconds'),
                       'seed': _integer(search.get('seed', 17), 0, 2147483647, 'seed')}}


def default_settings():
    return deepcopy(DEFAULT_SETTINGS)


def minute(value):
    if isinstance(value, str):
        hour, minutes = map(int, value[:5].split(':'))
        return hour * 60 + minutes
    return value.hour * 60 + value.minute


def validate_settings_grid(slots):
    seen = set()
    by_day = {}
    for slot in slots:
        if slot.start_time is None or slot.end_time is None:
            raise ValueError("Для нового режима каждому слоту нужны точные начало и конец.")
        if slot.start_time.second or slot.end_time.second or slot.start_time.microsecond or slot.end_time.microsecond:
            raise ValueError("Сетка поддерживает точность времени до минуты.")
        if not 0 <= slot.day_of_week <= 6 or slot.lesson_number < 1:
            raise ValueError("Некорректные день или номер пары сетки.")
        key = (slot.day_of_week, slot.lesson_number)
        if key in seen:
            raise ValueError("В сетке повторяется номер пары одного дня.")
        seen.add(key)
        start, end = minute(slot.start_time), minute(slot.end_time)
        if start >= end:
            raise ValueError("Начало пары должно быть раньше её окончания.")
        by_day.setdefault(slot.day_of_week, []).append((start, end, slot.lesson_number))
    if not slots:
        raise ValueError("Сетка недели пуста.")
    for timeline in by_day.values():
        timeline.sort()
        for left, right in zip(timeline, timeline[1:], strict=False):
            if left[1] > right[0] or left[2] >= right[2]:
                raise ValueError("Пары сетки пересекаются или номера не соответствуют порядку времени.")
