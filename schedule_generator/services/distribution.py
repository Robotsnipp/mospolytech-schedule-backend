"""Convex weekday load costs for actual attendance profiles, including personal PD."""

from collections import defaultdict

from .objectives import _attendance_profiles

BALANCE_PERSON_WEIGHT = 10
LATE_PERSON_PENALTY = 20


def collect_daily_distribution_penalties(model, specs, slots, pair_placement_vars, fixed_pd=()):
    """Sum floor(weight * people * daily_minutes² / 90²), Monday–Friday.

    Matching attendance profiles share variables, never six projects per group.
    Fixed PD uses the union of each person's literal intervals. Its constant
    contribution alone cannot move a lesson; additional load on that day costs
    progressively more. Saturday is excluded: it remains a separately penalized
    reserve day rather than a sixth day to fill for mathematical equality.
    """
    slots_by_day = defaultdict(list)
    for slot in slots:
        if slot.day_of_week < 5:
            slots_by_day[slot.day_of_week].append(slot)
    specs_by_id = {spec.event_id: spec for spec in specs}
    penalties = []
    for index, ((event_ids, fixed_intervals), people) in enumerate(sorted(_attendance_profiles(specs, fixed_pd).items())):
        weekdays = slots_by_day.keys() | {day for day, _, _ in fixed_intervals if day < 5}
        for day in sorted(weekdays):
            day_slots = slots_by_day[day]
            durations = {slot.id: ((slot.end_time.hour * 60 + slot.end_time.minute)
                                  - (slot.start_time.hour * 60 + slot.start_time.minute))
                         if slot.start_time is not None and slot.end_time is not None else 90
                         for slot in day_slots}
            fixed_minutes = sum(end - start for fixed_day, start, end in fixed_intervals if fixed_day == day)
            upper = fixed_minutes + max(durations.values(), default=0) * sum(specs_by_id[e].pairs_per_week for e in event_ids)
            if not upper:
                continue
            minutes = model.NewIntVar(0, upper, f"daily_minutes_{index}_{day}")
            model.Add(minutes == fixed_minutes + sum(
                durations[slot.id] * pair_placement_vars[event, slot.id]
                for event in event_ids for slot in day_slots))
            square = model.NewIntVar(0, upper * upper, f"daily_square_{index}_{day}")
            model.AddMultiplicationEquality(square, [minutes, minutes])
            factor = BALANCE_PERSON_WEIGHT * people
            cost = model.NewIntVar(0, factor * upper * upper // 8100, f"daily_balance_{index}_{day}")
            model.AddDivisionEquality(cost, factor * square, 8100)
            penalties.append(cost)
    return penalties
