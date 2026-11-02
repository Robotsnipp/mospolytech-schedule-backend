"""Personal rules translated to CP-SAT and ordered, budgeted optimization.

The finite rules share definitions and snapshots with planning_rules.py. No
cross-level giant weights are used: an optimum is fixed before the next level.
"""
from collections import defaultdict
from time import monotonic

from ortools.sat.python import cp_model

from ..enums import LessonType
from .constraints import (
    add_attendee_exclusivity,
    add_event_pair_vars,
    add_fixed_pd_constraints,
    add_location_consistency,
    add_room_exclusivity,
    add_teacher_exclusivity,
    link_onsite_room_vars,
)
from .dto import GenerationResult
from .planning_rules import attendance_profiles, occupied_numbers, validate_rules
from .settings import DAYS, POLICY_VERSION, minute
from .solution import extract_result


def _or(model, terms, name):
    terms = list(terms)
    if not terms:
        return 0
    if any(type(term) is int and term == 1 for term in terms):
        return 1
    terms = [term for term in terms if type(term) is not int or term]
    if not terms:
        return 0
    if len(terms) == 1:
        return terms[0]
    value = model.NewBoolVar(name)
    model.AddMaxEquality(value, terms)
    return value


def _and(model, terms, name):
    terms = list(terms)
    if any(type(term) is int and term == 0 for term in terms):
        return 0
    terms = [term for term in terms if type(term) is not int]
    if not terms:
        return 1
    if len(terms) == 1:
        return terms[0]
    value = model.NewBoolVar(name)
    model.AddMinEquality(value, terms)
    return value


def _not(value):
    return 1 - value if type(value) is int else value.Not()


def _excess(model, value, limit, bound, name):
    result = model.NewIntVar(0, max(0, bound), name)
    model.AddMaxEquality(result, [0, value - limit])
    return result


def _adjacency(model, timeline, prefix):
    """All consecutive active intervals, retaining exact start/end times."""
    result = []
    timeline = sorted(timeline, key=lambda node: (node['start'], node['end']))
    for index, left in enumerate(timeline):
        for right_index in range(index + 1, len(timeline)):
            right = timeline[right_index]
            if right['start'] < left['end']:
                continue
            between = [node['active'] for node in timeline[index + 1:right_index]
                       if node['start'] >= left['start']]
            edge = _and(model, [left['active'], right['active'], *[_not(term) for term in between]],
                        f'{prefix}_{index}_{right_index}')
            result.append((left, right, edge))
    return result


def build_personal_rules(model, snapshot, placement_vars, room_vars):
    rules = snapshot['settings']['rules']
    levels = [[] for _ in range(7)]
    rooms = {room['id']: room for room in snapshot['rooms']}
    slots = snapshot['slots']
    events = {event['id']: event for event in snapshot['events']}

    def impose(rule, amount, level, weight=1):
        if rule['mode'] == 'required':
            model.Add(amount == 0)
        else:
            levels[level].append(weight * amount)

    for pindex, profile in enumerate(attendance_profiles(snapshot)):
        weight = profile['weight']
        study_days = []
        for day in range(7):
            timeline = []
            onsite_timeline = []
            occupied = defaultdict(list)
            fixed_items = [snapshot['fixed_pd'][i] for i in profile['fixed_indices']
                           if snapshot['fixed_pd'][i]['day_of_week'] == day]
            for slot in [slot for slot in slots if slot['day_of_week'] == day]:
                terms = [placement_vars[(event_id, slot['id'])] for event_id in profile['event_ids']]
                active = _or(model, terms, f'active_{pindex}_{slot["id"]}')
                occupied[slot['lesson_number']].append(active)
                node = {'active': active, 'start': slot['start_minute'], 'end': slot['end_minute']}
                timeline.append(node)
                building_terms = defaultdict(list)
                onsite_terms = []
                for event_id in profile['event_ids']:
                    if events[event_id]['format'] != 'ONSITE':
                        continue
                    onsite_terms.append(placement_vars[(event_id, slot['id'])])
                    for room_id, room in rooms.items():
                        choice = room_vars.get((event_id, slot['id'], room_id))
                        if choice is not None and room.get('building_id'):
                            building_terms[f"building:{room['building_id']}"].append(choice)
                onsite_timeline.append({**node, 'active': _or(model, onsite_terms, f'onsite_{pindex}_{slot["id"]}'),
                    'buildings': {building: _or(model, values, f'building_{pindex}_{slot["id"]}_{building}')
                                  for building, values in building_terms.items()}})
            for fixed in fixed_items:
                numbers = occupied_numbers(day, fixed['start_minute'], fixed['end_minute'], slots)
                if not numbers:
                    for key in ('max_pairs_per_day', 'min_pairs_per_day', 'max_consecutive_pairs'):
                        if key in rules and rules[key]['mode'] == 'required':
                            raise ValueError('Фиксированная ПД вне сетки: обязательный подсчёт занятых пар неполон.')
                for number in numbers:
                    occupied[number].append(1)
                node = {'active': 1, 'start': fixed['start_minute'], 'end': fixed['end_minute']}
                timeline.append(node)
                room = rooms.get(fixed.get('room_id'))
                building = f"building:{room['building_id']}" if room and room.get('building_id') else (
                    f"external:{fixed['place_raw']}" if fixed.get('place_raw') else None)
                if 'max_building_changes' in rules and rules['max_building_changes']['mode'] == 'required' and building is None:
                    raise ValueError('Неизвестно место фиксированной ПД: обязательное число смен корпуса проверить нельзя.')
                onsite_timeline.append({**node, 'buildings': {building: 1} if building else {}})
            occupied_vars = {number: _or(model, terms, f'occ_{pindex}_{day}_{number}')
                             for number, terms in occupied.items()}
            active_day = _or(model, occupied_vars.values(), f'day_{pindex}_{day}')
            # A PD outside the bell grid is still a study day.
            if fixed_items:
                active_day = 1
            study_days.append(active_day)
            for free in rules.get('free_days', []):
                if DAYS[day] == free['day']:
                    impose(free, active_day, 0, weight)
            for key in ('earliest_start', 'latest_end'):
                if key not in rules:
                    continue
                rule = rules[key]
                boundary = minute(rule['value'])
                violations = []
                for node in timeline:
                    excess = max(0, boundary - node['start']) if key == 'earliest_start' else max(0, node['end'] - boundary)
                    if excess:
                        violations.append(excess * node['active'])
                if violations:
                    boundary_excess = model.NewIntVar(0, 1440, f'{key}_{pindex}_{day}')
                    model.AddMaxEquality(boundary_excess, violations)
                    impose(rule, boundary_excess, 1, weight)
            if 'max_pairs_per_day' in rules:
                rule = rules['max_pairs_per_day']
                impose(rule, _excess(model, sum(occupied_vars.values()), rule['value'], len(occupied_vars), f'max_pairs_{pindex}_{day}'), 3, weight)
            if 'min_pairs_per_day' in rules:
                rule = rules['min_pairs_per_day']
                deficit = _excess(model, rule['value'] * active_day, sum(occupied_vars.values()), rule['value'], f'min_pairs_{pindex}_{day}')
                impose(rule, deficit, 3, weight)
            if 'max_consecutive_pairs' in rules:
                rule = rules['max_consecutive_pairs']
                limit = rule['value']
                previous_number, previous_length = None, 0
                lengths = []
                for number in sorted(occupied_vars):
                    active = occupied_vars[number]
                    length = model.NewIntVar(0, len(occupied_vars), f'series_{pindex}_{day}_{number}')
                    preceding = previous_length if previous_number == number - 1 else 0
                    model.Add(length == preceding + 1).OnlyEnforceIf(active)
                    model.Add(length == 0).OnlyEnforceIf(_not(active))
                    lengths.append(length)
                    previous_number, previous_length = number, length
                if lengths:
                    longest = model.NewIntVar(0, len(lengths), f'longest_{pindex}_{day}')
                    model.AddMaxEquality(longest, lengths)
                    impose(rule, _excess(model, longest, limit, len(lengths), f'series_excess_{pindex}_{day}'), 3, weight)
            if 'max_break_minutes' in rules:
                rule = rules['max_break_minutes']
                for left, right, edge in _adjacency(model, timeline, f'gap_{pindex}_{day}'):
                    gap_excess = max(0, right['start'] - left['end'] - rule['value'])
                    if gap_excess:
                        impose(rule, gap_excess * edge, 2, weight)
            if 'max_building_changes' in rules:
                rule = rules['max_building_changes']
                changes = []
                for eindex, (left, right, edge) in enumerate(_adjacency(model, onsite_timeline, f'transition_{pindex}_{day}')):
                    for left_building, left_value in left['buildings'].items():
                        for right_building, right_value in right['buildings'].items():
                            if left_building != right_building:
                                changes.append(_and(model, [edge, left_value, right_value], f'change_{pindex}_{day}_{eindex}_{left_building}_{right_building}'))
                impose(rule, _excess(model, sum(changes), rule['value'], len(onsite_timeline), f'changes_{pindex}_{day}'), 5, weight)
        if 'max_study_days' in rules:
            rule = rules['max_study_days']
            impose(rule, _excess(model, sum(study_days), rule['value'], 7, f'study_days_{pindex}'), 4, weight)
    if 'preferred_building' in rules:
        rule = rules['preferred_building']
        for (event_id, _slot_id, room_id), variable in room_vars.items():
            if rooms[room_id].get('building_id') != rule['value']:
                impose(rule, variable, 6, max(1, events[event_id]['audience_size']))
    return levels


def solve_settings(*, slots, rooms, specs, fixed_pd, snapshot, config):
    # Import variable construction only; the legacy objective is intentionally not built.
    from .generator import _build_variables

    start = monotonic()
    model = cp_model.CpModel()
    slot_ids = [slot.id for slot in slots]
    room_ids = [room.id for room in rooms]
    day_by_id = {slot.id: slot.day_of_week for slot in slots}
    number_by_id = {slot.id: slot.lesson_number for slot in slots}
    place, room_vars, suitable = _build_variables(model, specs, slot_ids, room_ids, {room.id: room.capacity for room in rooms})
    add_room_exclusivity(model, room_vars)
    add_attendee_exclusivity(model, specs, slot_ids, place)
    add_teacher_exclusivity(model, specs, slot_ids, place)
    add_fixed_pd_constraints(model, specs, fixed_pd, slots, place, room_vars)
    add_location_consistency(model, specs, slot_ids, place, room_vars,
                             {room.id: room.building.location_id for room in rooms if room.building_id},
                             day_by_id, number_by_id)
    add_event_pair_vars(model, specs, slot_ids, place)
    link_onsite_room_vars(model, specs, slot_ids, place, room_vars, suitable)
    # New weekly policy permits every day for ordinary classes; fixed PD never moves.
    for spec in specs:
        if spec.lesson_type == LessonType.PD:
            for slot in slots:
                if slot.day_of_week not in spec.allowed_days:
                    model.Add(place[(spec.event_id, slot.id)] == 0)
    changes_rule = snapshot['settings']['rules'].get('max_building_changes')
    if changes_rule and changes_rule['mode'] == 'required':
        known_buildings = {room['id']: room.get('building_id') for room in snapshot['rooms']}
        for (_event, _slot, room_id), variable in room_vars.items():
            if known_buildings.get(room_id) is None:
                model.Add(variable == 0)
    user_levels = build_personal_rules(model, snapshot, place, room_vars)
    completeness = sum(place.values())
    lectures = sum(variable for (event_id, _), variable in place.items()
                   if next(spec for spec in specs if spec.event_id == event_id).lesson_type == LessonType.LECTURE)
    # Existing thematic preferences break ties only after all user levels.
    from .teaching_preferences import collect_teaching_preferences
    teaching = collect_teaching_preferences(model=model, specs=specs, slots=slots, pair_placement_vars=place)
    levels = [('completeness', completeness, True), ('lectures', lectures, True)] + [
        (name, sum(terms), False) for name, terms in zip(
            ('free_days', 'time_bounds', 'breaks', 'daily_load', 'study_days', 'building_changes', 'preferred_building'), user_levels, strict=True)]
    levels += [('teaching_order', teaching['lecture_applied'] - teaching['applied_lecture'] + teaching['subject_blocks'], True)]
    # Existing convenience breaks remaining ties, without another Saturday/late cost.
    from .objectives import (
        collect_chronotype_penalties,
        collect_fatigue_penalties,
        collect_teacher_overload_penalties,
        collect_window_penalties,
    )
    windows = collect_window_penalties(model, specs, slot_ids, day_by_id, place, slots=slots, fixed_pd=fixed_pd)
    fatigue, peaks = collect_fatigue_penalties(model, specs, slot_ids, day_by_id, number_by_id, place)
    teachers = {teacher['id']: teacher for teacher in snapshot.get('teachers', [])}
    overload = collect_teacher_overload_penalties(model, specs, day_by_id, place, teachers)
    chronotype = collect_chronotype_penalties(specs, slot_ids, number_by_id, place, teachers)
    levels += [('general_convenience', sum(windows) + 2*sum(fatigue) + sum(peaks) + 60*sum(overload) + 2*sum(chronotype), False)]
    validation = model.Validate()
    if validation:
        return GenerationResult('ERROR', None, monotonic() - start, [], [spec.event_id for spec in specs], validation)
    incumbent = None
    diagnostics = []
    timed_out = False
    last_status = 'UNKNOWN'
    for name, expression, maximize in levels:
        remaining = config.time_limit_seconds - (monotonic() - start)
        if remaining <= 0:
            timed_out = True
            break
        if maximize:
            model.Maximize(expression)
        else:
            model.Minimize(expression)
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = remaining
        solver.parameters.random_seed = config.seed
        solver.parameters.num_search_workers = config.num_search_workers
        status = solver.Solve(model)
        last_status = solver.StatusName(status)
        feasible = status in (cp_model.OPTIMAL, cp_model.FEASIBLE)
        diagnostic = {'level': name, 'status': last_status, 'proven_optimal': status == cp_model.OPTIMAL,
                      'value': solver.Value(expression) if feasible else None,
                      'best_bound': solver.BestObjectiveBound(), 'wall_time_seconds': solver.WallTime()}
        diagnostics.append(diagnostic)
        if feasible:
            incumbent = extract_result(solver=solver, status_name=last_status, feasible=True, specs=specs,
                                       room_ids=room_ids, pair_placement_vars=place, onsite_room_choice_vars=room_vars)
            incumbent.objective_value = None
        if status != cp_model.OPTIMAL:
            timed_out = status in (cp_model.UNKNOWN, cp_model.FEASIBLE)
            break
        value = solver.Value(expression)
        model.Add(expression == value)
        # Hint the complete incumbent at the next stage; preserve it outside solver.
        model.ClearHints()
        for variable in [*place.values(), *room_vars.values()]:
            model.AddHint(variable, solver.Value(variable))
    if incumbent is None:
        incumbent = GenerationResult('INFEASIBLE' if last_status == 'INFEASIBLE' else 'UNKNOWN', None, 0, [],
                                     [spec.event_id for spec in specs], f'status={last_status}')
    elapsed = monotonic() - start
    incumbent.solve_time = elapsed
    incumbent.solver_diagnostics = {
        'status': last_status, 'preferences': POLICY_VERSION, 'policy_version': POLICY_VERSION,
        'proven_optimal': not timed_out and len(diagnostics) == len(levels) and all(level['proven_optimal'] for level in diagnostics),
        'levels': diagnostics, 'quality_vector': [item['value'] for item in diagnostics],
        'unfinished_levels': [item['level'] for item in diagnostics if not item['proven_optimal']] + [name for name, _, _ in levels[len(diagnostics):]],
        'time_limit_seconds': config.time_limit_seconds, 'wall_time_seconds': elapsed,
    }
    incumbent.message = f'{incumbent.status}: размещено {len(incumbent.assignments)} пар; политика {POLICY_VERSION}'
    report = validate_rules(snapshot, incumbent.assignments)
    if incumbent.status in ('SOLVED', 'PARTIAL') and report['blocking']:
        raise ValueError('Результат не прошёл независимую проверку обязательных условий: ' + str(report['blocking']))
    return incumbent
