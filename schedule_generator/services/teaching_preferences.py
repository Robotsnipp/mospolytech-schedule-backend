"""Soft teaching preferences scoped by subject and actual common group identities."""

from collections import defaultdict

from ..enums import LessonType

PREFERENCE_VERSION = "weekday-distribution-v3"
# One extra cohort/subject adjacency is worth 60 person-minutes of waiting;
# it cannot outweigh a multi-hour gap for an entire group.
SUBJECT_BLOCK_REWARD = 60
SATURDAY_PERSON_PENALTY = 60
REGULAR_TYPES = frozenset({LessonType.LECTURE, LessonType.PRACTICE, LessonType.LAB})
APPLIED_TYPES = frozenset({LessonType.PRACTICE, LessonType.LAB})


def _and(model, left, right, name):
    value = model.NewBoolVar(name)
    model.Add(value <= left)
    model.Add(value <= right)
    model.Add(value >= left + right - 1)
    return value


def _count(model, values, bound, name):
    value = model.NewIntVar(0, bound, name)
    model.Add(value == sum(values))
    return value


def collect_teaching_preferences(*, model, specs, slots, pair_placement_vars):
    """Exact counts; bounds follow required occurrences, not all candidate edges.

    A lecture stream and a group share a context only for their common group id.
    Teachers, stream ids or similar subject labels alone never create a match.
    One lecture may benefit each attending group; there is no reward for PD/PE.
    """
    saturday = _count(model, [pair_placement_vars[spec.event_id, slot.id]
                             for spec in specs for slot in slots if slot.day_of_week == 5],
                      sum(spec.pairs_per_week for spec in specs), "saturday_pairs")
    people = {spec.event_id: (len(spec.attending_student_ids or spec.blocking_student_ids)
                             or max(1, spec.audience_size)) + int(spec.teacher_id is not None)
              for spec in specs}
    saturday_people = _count(model, [people[spec.event_id] * pair_placement_vars[spec.event_id, slot.id]
                                    for spec in specs for slot in slots if slot.day_of_week == 5],
                             sum(people[spec.event_id] * spec.pairs_per_week for spec in specs),
                             "saturday_person_pairs")
    late_people = _count(model, [people[spec.event_id] * pair_placement_vars[spec.event_id, slot.id]
                                for spec in specs for slot in slots if slot.lesson_number >= 6],
                         sum(people[spec.event_id] * spec.pairs_per_week for spec in specs),
                         "late_person_pairs")
    contexts = defaultdict(list)
    for spec in specs:
        if spec.subject_id is not None and spec.lesson_type in REGULAR_TYPES:
            for group_id in spec.blocking_group_ids:
                contexts[spec.subject_id, group_id].append(spec)
    adjacent = []
    ordered = sorted(slots, key=lambda slot: (slot.day_of_week, slot.lesson_number, slot.id))
    for left, right in zip(ordered, ordered[1:], strict=False):
        if left.day_of_week != right.day_of_week or right.lesson_number != left.lesson_number + 1:
            continue
        if left.end_time and right.start_time and left.end_time > right.start_time:
            continue
        adjacent.append((left.id, right.id))
    blocks, teaching, reversed_teaching = [], [], []
    blocks_bound = teaching_bound = 0
    for (subject, group), events in sorted(contexts.items()):
        occurrence_count = sum(event.pairs_per_week for event in events)
        if occurrence_count < 2:
            continue
        blocks_bound += occurrence_count - 1
        lecture_events = [event for event in events if event.lesson_type == LessonType.LECTURE]
        applied_events = [event for event in events if event.lesson_type in APPLIED_TYPES]
        teaching_bound += min(sum(event.pairs_per_week for event in lecture_events),
                              sum(event.pairs_per_week for event in applied_events))
        busy, lectures, applied = {}, {}, {}
        for slot in slots:
            for label, subset, target in (("any", events, busy), ("lecture", lecture_events, lectures),
                                           ("applied", applied_events, applied)):
                if subset:
                    value = model.NewBoolVar(f"subject_{subject}_g{group}_{label}_{slot.id}")
                    model.AddMaxEquality(value, [pair_placement_vars[e.event_id, slot.id] for e in subset])
                    target[slot.id] = value
        for before, after in adjacent:
            blocks.append(_and(model, busy[before], busy[after], f"block_s{subject}_g{group}_{before}_{after}"))
            if lectures and applied:
                teaching.append(_and(model, lectures[before], applied[after],
                                     f"teaching_s{subject}_g{group}_{before}_{after}"))
                reversed_teaching.append(_and(model, applied[before], lectures[after],
                                              f"reverse_s{subject}_g{group}_{before}_{after}"))
    return {"saturday": saturday,
            "saturday_people": saturday_people,
            "late_people": late_people,
            "lecture_applied": _count(model, teaching, teaching_bound, "lecture_applied_pairs"),
            "applied_lecture": _count(model, reversed_teaching, teaching_bound, "applied_lecture_pairs"),
            "subject_blocks": _count(model, blocks, blocks_bound, "subject_block_pairs")}
