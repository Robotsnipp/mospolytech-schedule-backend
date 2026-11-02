"""One semester command, one immutable weekly template per module.

Loads are never merged into a fictitious common weekly load. The shared search
budget is divided fairly before starting, so a hard module cannot starve later
modules. A parent record is orchestration history, never a solver run.
"""
import json
from copy import deepcopy
from datetime import date, timedelta
from fractions import Fraction
from hashlib import sha256
from time import monotonic

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404

from ..enums import LessonType
from ..models import (
    FixedProjectSession,
    GenerationRun,
    SemesterGeneration,
    SemesterGenerationModule,
)
from .bells import validate_bell_pattern
from .curriculum import validate_prepared_load
from .event_specs import build_event_specs
from .fixed_pd import load_fixed_pd
from .generation import GenerationError, run_generation, select_events
from .planning_snapshot import build_snapshot
from .resource_profiles import resolve_resource_profile
from .settings import normalize_settings, validate_settings_grid


class SemesterGenerationError(ValueError):
    pass


def _selected_events(semester, module, group):
    fixed_projects = FixedProjectSession.objects.filter(project__semester=semester).values('project_id')
    return select_events(semester, module).filter(
        Q(group=group) | Q(stream__groups=group) | Q(project__enrollments__student__group=group)
    ).exclude(lesson_type=LessonType.PE).exclude(lesson_type=LessonType.PD, project_id__in=fixed_projects).distinct()



def _number(value):
    return value.numerator if value.denominator == 1 else float(value)


def _hours_per_pair(event, module, hours):
    explicit = event.preparation_source.get('academic_hours_per_pair')
    weekly_pairs = event.pairs_per_week * module.teaching_weeks
    if explicit is not None:
        if type(explicit) is not int or explicit < 1:
            raise SemesterGenerationError(f'Занятие {event.pk}: некорректное число академических часов в паре.')
        factor = Fraction(explicit)
        if hours is not None and hours != weekly_pairs * factor:
            raise SemesterGenerationError(f'Модуль {module.number}: часы «{event.subject.name}» не соответствуют сохранённому размеру пары и учебным неделям.')
        return factor, 'preparation_source.academic_hours_per_pair'
    if hours is not None and weekly_pairs:
        factor = Fraction(hours, weekly_pairs)
        if factor <= 0:
            raise SemesterGenerationError(f'Занятие {event.pk}: положительная недельная нагрузка имеет нулевые часы.')
        return factor, 'academic_hours / (pairs_per_week * teaching_weeks)'
    return None, None


def _coverage(semester_start_date, modules):
    start = date.fromisoformat(semester_start_date)
    gaps = []
    previous_end, previous_id = None, None
    for module in modules:
        module_start = date.fromisoformat(module['start_date'])
        gap_start = previous_end + timedelta(days=1) if previous_end is not None else start
        if module_start > gap_start:
            gaps.append({'start_date': gap_start.isoformat(),
                         'end_date': (module_start - timedelta(days=1)).isoformat(),
                         'kind': 'between_modules' if previous_end is not None else 'before_first_module',
                         'before_module_id': module['module_id'], 'after_module_id': previous_id})
        previous_end, previous_id = date.fromisoformat(module['end_date']), module['module_id']
    return {'basis': 'configured_modules', 'start_date': modules[0]['start_date'], 'end_date': modules[-1]['end_date'],
            'semester_start_date': semester_start_date, 'semester_end_date': None,
            'calendar_complete': None, 'trailing_gap_unknown': True,
            'starts_at_semester_start': modules[0]['start_date'] == semester_start_date,
            'continuous_between_modules': not any(gap['kind'] == 'between_modules' for gap in gaps),
            'gaps': gaps, 'module_count': len(modules),
            'notice': 'Рассчитаны заданные модули. Конец календарного семестра не задан; число учебных недель не определяет его дату.'}


def _fingerprint(snapshot):
    # Lists with database identities are sets for comparison; normalize their
    # order without changing the saved, readable snapshot itself.
    canonical = deepcopy(snapshot)
    for key, identity in [('events', 'id'), ('slots', 'id'), ('rooms', 'id'), ('students', 'id'),
                          ('teachers', 'id'), ('fixed_pd', 'session_id')]:
        canonical[key] = sorted(canonical.get(key, []), key=lambda row: row[identity])
    return sha256(json.dumps(canonical, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _capture_inputs(*, semester, module, group, week_pattern, settings, resource_profile):
    module.refresh_from_db()
    group.refresh_from_db()
    week_pattern.refresh_from_db()
    events = _selected_events(semester, module, group)
    specs, skipped = build_event_specs(events)
    if skipped:
        raise SemesterGenerationError('Входная нагрузка изменилась: появились неполные занятия.')
    for spec in specs:
        if spec.lesson_type != LessonType.PD:
            spec.allowed_days = frozenset(range(7))
    slots = list(week_pattern.slots.order_by('day_of_week', 'lesson_number'))
    validate_settings_grid(slots)
    _, rooms = resolve_resource_profile(resource_profile, semester, group)
    fixed_pd, _ = load_fixed_pd(semester=semester, module=module, week_pattern=week_pattern, slots=slots)
    captured = build_snapshot(group=group, settings=settings, slots=slots, specs=specs, events=events,
                              rooms=rooms, fixed_pd=fixed_pd, semester=semester, module=module, week_pattern=week_pattern)
    captured['resource_profile'] = resource_profile
    return captured


def _actual_manifest(expected, snapshot):
    """Keep a changed child's actual academic totals, never preflight fiction."""
    actual = deepcopy(expected)
    actual['teaching_weeks'] = snapshot['module_teaching_weeks']
    actual['module_number'] = snapshot['module_number']
    actual['start_date'] = snapshot['module_start_date']
    actual['end_date'] = snapshot['module_end_date']
    actual['events'] = []
    for event in snapshot['events']:
        hours = event.get('academic_hours') if event.get('module_id') == expected['module_id'] else None
        denominator = event['pairs_per_week'] * actual['teaching_weeks']
        factor = Fraction(hours, denominator) if hours is not None and denominator else None
        actual['events'].append({'event_id': event['id'], 'pairs_per_week': event['pairs_per_week'],
            'academic_hours': hours, 'hours_per_pair_ratio': [factor.numerator, factor.denominator] if factor is not None else None,
            'hours_unknown_reason': None if hours is not None else 'Часы в изменённом входном снимке не распределены по модулю.'})
    actual['hours_complete'] = all(event['academic_hours'] is not None for event in actual['events'])
    actual['known_required_hours'] = sum(event['academic_hours'] or 0 for event in actual['events'])
    actual['required_hours'] = actual['known_required_hours'] if actual['hours_complete'] else None
    actual['weekly_required_pairs'] = sum(event['pairs_per_week'] for event in actual['events'])
    return actual

def _preflight(*, semester, group, week_pattern, settings, resource_profile):
    settings = normalize_settings(settings)
    if group.semester_id != semester.pk or not group.students.exists():
        raise SemesterGenerationError('Нужна группа выбранного семестра с известным составом.')
    if week_pattern is None:
        week_pattern = semester.week_patterns.order_by('id').first()
    if week_pattern is None or week_pattern.semester_id != semester.pk:
        raise SemesterGenerationError('Нужна сетка звонков выбранного семестра.')
    _, rooms = resolve_resource_profile(resource_profile, semester, group)
    modules = list(semester.modules.order_by('start_date', 'end_date', 'id'))
    if not modules:
        raise SemesterGenerationError('Для расчёта семестра задайте календарные модули и их нагрузку.')
    total_budget = settings['search']['time_limit_seconds']
    if total_budget < len(modules):
        raise SemesterGenerationError(f'Общий лимит должен быть не меньше {len(modules)} секунд: каждому модулю нужна хотя бы секунда поиска.')
    previous_end = None
    slots = list(week_pattern.slots.order_by('day_of_week', 'lesson_number'))
    validate_settings_grid(slots)
    manifest = []
    for index, module in enumerate(modules):
        if module.start_date < semester.start_date or module.end_date < module.start_date:
            raise SemesterGenerationError(f'Модуль {module.number}: даты некорректны или начинаются раньше семестра.')
        if previous_end is not None and module.start_date <= previous_end:
            raise SemesterGenerationError('Даты модулей пересекаются; недельный шаблон по дате выбрать однозначно нельзя.')
        previous_end = module.end_date
        events = _selected_events(semester, module, group)
        validate_bell_pattern(week_pattern, slots, events)
        validate_prepared_load(events)
        specs, skipped = build_event_specs(events)
        if skipped:
            raise SemesterGenerationError(f'Модуль {module.number}: неполные входные данные — {skipped[0][1]}')
        if not specs or not any(spec.pairs_per_week > 0 for spec in specs):
            raise SemesterGenerationError(f'Модуль {module.number}: нет подготовленной обычной нагрузки выбранной группы.')
        if any(spec.blocking_group_ids - {group.pk} for spec in specs):
            raise SemesterGenerationError('Поток нескольких групп не поддерживается расчётом выбранной группы.')
        event_rows = []
        for event in events.select_related('subject').order_by('pk'):
            hours = event.academic_hours if event.module_id == module.pk else None
            # Common semester hours have no explicit per-module allocation. Do not
            # duplicate that figure in every module or manufacture its distribution.
            reason = None
            if event.module_id is None:
                reason = 'Общая семестровая нагрузка: часы по модулям не распределены.'
            elif hours is None:
                reason = 'Академические часы занятия не заданы.'
            factor, factor_source = _hours_per_pair(event, module, hours)
            event_rows.append({'event_id': event.pk, 'subject_name': event.subject.name,
                               'module_id': event.module_id, 'pairs_per_week': event.pairs_per_week,
                               'academic_hours': hours, 'semester_academic_hours': event.academic_hours,
                               'hours_per_pair': _number(factor) if factor is not None else None,
                               'hours_per_pair_ratio': [factor.numerator, factor.denominator] if factor is not None else None,
                               'hours_per_pair_source': factor_source,
                               'hours_unknown_reason': reason})
        complete_hours = all(event['academic_hours'] is not None for event in event_rows)
        known_hours = sum(event['academic_hours'] or 0 for event in event_rows)
        manifest.append({'module_id': module.pk, 'module_number': module.number,
                         'start_date': module.start_date.isoformat(), 'end_date': module.end_date.isoformat(),
                         'teaching_weeks': module.teaching_weeks, 'events': event_rows,
                         'weekly_required_pairs': sum(spec.pairs_per_week for spec in specs),
                         'required_hours': known_hours if complete_hours else None,
                         'known_required_hours': known_hours, 'hours_complete': complete_hours,
                         'budget_seconds': total_budget // len(modules) + (1 if index < total_budget % len(modules) else 0)})
        child_settings = deepcopy(settings)
        child_settings['search']['time_limit_seconds'] = manifest[-1]['budget_seconds']
        captured = _capture_inputs(semester=semester, module=module, group=group, week_pattern=week_pattern,
                                   settings=child_settings, resource_profile=resource_profile)
        expected_events = {(event['event_id'], event['module_id'], event['subject_name'], event['pairs_per_week'], event['semester_academic_hours'])
                           for event in event_rows}
        captured_events = {(event['id'], event['module_id'], event['subject_name'], event['pairs_per_week'], event['academic_hours'])
                           for event in captured['events']}
        if (captured['module_teaching_weeks'] != manifest[-1]['teaching_weeks']
                or captured['module_start_date'] != manifest[-1]['start_date']
                or captured['module_end_date'] != manifest[-1]['end_date']
                or expected_events != captured_events or captured['rooms'] != rooms):
            raise SemesterGenerationError('Исходные данные изменились во время подготовки расчёта. Повторите запуск с актуальной нагрузкой.')
        manifest[-1]['planning_input'] = captured
        manifest[-1]['input_fingerprint'] = _fingerprint(captured)
    return settings, week_pattern, modules, manifest, rooms


def _module_result(manifest, run=None, error=None):
    result = {key: value for key, value in manifest.items() if key not in ('events', 'planning_input')}
    result.update(run_id=run.pk if run else None, status=run.status if run else 'NOT_STARTED',
                  scheduled_count=run.scheduled_classes.count() if run else 0,
                  weekly_scheduled_pairs=run.scheduled_classes.count() if run else 0,
                  scheduled_hours=0 if manifest['hours_complete'] else None,
                  known_scheduled_hours=0, solve_time_seconds=run.solve_time_seconds or 0 if run else 0,
                  message=str(error) if error else (run.message if run else ''),
                  hours_unknown_reasons=sorted({event['hours_unknown_reason'] for event in manifest['events'] if event['hours_unknown_reason']}))
    if run:
        # Count the persisted genuine placements, using only the frozen module
        # weeks and frozen knowledge of which events have confirmed load hours.
        counts = {}
        for event_id in run.scheduled_classes.values_list('event_id', flat=True):
            counts[event_id] = counts.get(event_id, 0) + 1
        known = sum((counts.get(event['event_id'], 0) * manifest['teaching_weeks'] * Fraction(*event['hours_per_pair_ratio'])
                     for event in manifest['events'] if event['academic_hours'] is not None and event['hours_per_pair_ratio'] is not None), Fraction())
        result['known_scheduled_hours'] = _number(known)
        result['known_scheduled_hours_exact'] = [known.numerator, known.denominator]
        result['scheduled_hours'] = _number(known) if manifest['hours_complete'] else None
    if error:
        result['status'] = 'ERROR'
    return result


def _status(results):
    statuses = [row['status'] for row in results]
    if all(value == 'SOLVED' for value in statuses):
        return 'SOLVED'
    if 'PARTIAL' in statuses or any(row['scheduled_count'] for row in results):
        return 'PARTIAL'
    if 'ERROR' in statuses:
        return 'ERROR'
    if all(value == 'INFEASIBLE' for value in statuses):
        return 'INFEASIBLE'
    if 'PENDING' in statuses or 'NOT_STARTED' in statuses:
        return 'PENDING'
    return 'UNKNOWN'


def generate_semester(*, semester, group, settings, resource_profile, week_pattern=None):
    try:
        normalized, week_pattern, modules, manifest, rooms = _preflight(
            semester=semester, group=group, week_pattern=week_pattern, settings=settings,
            resource_profile=resource_profile)
    except (ValueError, ValidationError) as exc:
        raise SemesterGenerationError(str(exc)) from exc
    start = monotonic()
    results = [_module_result(item) for item in manifest]
    with transaction.atomic():
        batch = SemesterGeneration.objects.create(
            semester=semester, group=group, week_pattern=week_pattern, resource_profile=resource_profile,
            settings_snapshot=normalized, module_results=results,
            input_snapshot={'schema_version': 1, 'kind': 'module-weekly-templates',
                            'semester_name': semester.name, 'group_name': group.name,
                            'semester_start_date': semester.start_date.isoformat(),
                            'semester_weeks_count': semester.weeks_count, 'week_pattern_name': week_pattern.name,
                            'modules': manifest, 'rooms': rooms,
                            'coverage': _coverage(semester.start_date.isoformat(), manifest),
                            'search_budget_policy': 'equal-integer-slices-v1',
                            'scope': 'Отдельный недельный шаблон каждого модуля. Отмены и переносы конкретных дат не моделируются.'})
        children = [SemesterGenerationModule.objects.create(batch=batch, module=module,
                    budget_seconds=item['budget_seconds'], result=result)
                    for module, item, result in zip(modules, manifest, results, strict=True)]
    for index, (module, item, child) in enumerate(zip(modules, manifest, children, strict=True)):
        child_settings = deepcopy(normalized)
        child_settings['search']['time_limit_seconds'] = item['budget_seconds']
        run, error, actual_manifest = None, None, item
        input_changed = False
        actual_fingerprint = None
        try:
            try:
                fresh = _capture_inputs(semester=semester, module=module, group=group, week_pattern=week_pattern,
                                        settings=child_settings, resource_profile=resource_profile)
            except (ValueError, ValidationError) as exc:
                input_changed = True
                raise SemesterGenerationError('Исходные данные изменились или стали неполными после подготовки расчёта: ' + str(exc)) from exc
            if _fingerprint(fresh) != item['input_fingerprint']:
                input_changed = True
                raise SemesterGenerationError('Исходные данные изменились после подготовки семестрового расчёта. Модуль не запущен; выполните новый расчёт.')
            outcome = run_generation(semester=semester, module=module, group=group,
                week_pattern=week_pattern, resource_profile=resource_profile, settings=child_settings,
                clear_previous=False)
            run = GenerationRun.objects.get(pk=outcome.run_id)
            actual_fingerprint = _fingerprint(run.input_snapshot)
            if actual_fingerprint != item['input_fingerprint']:
                input_changed = True
                actual_manifest = _actual_manifest(item, run.input_snapshot)
                raise SemesterGenerationError('Исходные данные изменились во время расчёта модуля. Фактический запуск сохранён отдельно; семестровый расчёт не завершён.')
        except GenerationError as exc:
            error = exc
            run = GenerationRun.objects.filter(pk=getattr(exc, 'run_id', None)).first()
        except Exception as exc:  # noqa: BLE001 — keep partial orchestration history on unexpected failures
            error = exc
            run = run or GenerationRun.objects.filter(pk=getattr(exc, 'run_id', None)).first()
        results[index] = _module_result(actual_manifest, run, error)
        results[index].update(input_changed=input_changed, expected_required_hours=item['required_hours'],
                              expected_input_fingerprint=item['input_fingerprint'], actual_input_fingerprint=actual_fingerprint)
        with transaction.atomic():
            child.run = run
            child.result = results[index]
            child.save(update_fields=['run', 'result'])
            batch.module_results = deepcopy(results)
            batch.elapsed_seconds = monotonic() - start
            batch.solve_time_seconds = sum(row['solve_time_seconds'] for row in results)
            batch.status = _status(results) if index == len(modules) - 1 else 'PENDING'
            batch.message = 'Каждый модуль рассчитан отдельно; недельные нагрузки модулей не складываются.'
            batch.save(update_fields=['module_results', 'elapsed_seconds', 'solve_time_seconds', 'status', 'message'])
    return read_semester_generation(batch.pk)


def read_semester_generation(batch_id):
    batch = get_object_or_404(SemesterGeneration, pk=batch_id)
    rows = batch.module_results
    hours_complete = all(row['hours_complete'] for row in rows)
    return {'id': batch.pk, 'semester': batch.semester_id, 'group': batch.group_id,
            'week_pattern': batch.week_pattern_id, 'resource_profile': batch.resource_profile,
            'created_at': batch.created_at.isoformat(), 'status': batch.status,
            'settings_snapshot': batch.settings_snapshot, 'input_snapshot': batch.input_snapshot,
            'coverage': batch.input_snapshot.get('coverage') or _coverage(batch.input_snapshot['semester_start_date'], rows),
            'modules': rows, 'module_results': rows, 'weekly_template_count': len(rows),
            'required_hours': sum(row['required_hours'] for row in rows) if hours_complete else None,
            'scheduled_hours': sum(row['scheduled_hours'] for row in rows) if hours_complete else None,
            'known_required_hours': sum(row['known_required_hours'] for row in rows),
            'known_scheduled_hours': sum(row['known_scheduled_hours'] for row in rows),
            'hours_complete': hours_complete, 'totals_complete': hours_complete,
            'complete': batch.status == 'SOLVED',
            'scheduled_count': sum(row['scheduled_count'] for row in rows),
            'solve_time_seconds': batch.solve_time_seconds, 'elapsed_seconds': batch.elapsed_seconds,
            'message': batch.message}


def list_semester_generations(*, semester=None, group=None):
    batches = SemesterGeneration.objects.all()
    if semester is not None:
        batches = batches.filter(semester_id=semester)
    if group is not None:
        batches = batches.filter(group_id=group)
    return [read_semester_generation(batch.pk) for batch in batches]
