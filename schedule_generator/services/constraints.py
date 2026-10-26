"""Жёсткие (hard) ограничения модели CP-SAT.

Здесь живут правила «невозможных» комбинаций — решение обязано им следовать.
Каждое ограничение — отдельная функция: легко добавлять новые правила и
легко читать сборку модели в generator.py.

Обозначения:
- pair_placement_vars[event_id, slot_id] — булева переменная «пара события
  ставится в слот»;
- onsite_room_choice_vars[event_id, slot_id, room_id] — булева переменная
  «очная пара в слоте в данной аудитории».
"""

from __future__ import annotations

from collections import defaultdict

from ortools.sat.python import cp_model

from ..enums import Format, LessonType
from .dto import EventSpec


def add_lecture_single_slot(model: cp_model.CpModel, specs: list[EventSpec],
                            slot_ids: list[int], pair_placement_vars: dict) -> None:
    """Лекция одного потока идёт строго в один слот недели:
    все группы потока слушают её одновременно; аудитория не нужна."""
    for spec in specs:
        if spec.stream_id is not None:
            model.AddAtMostOne(
                pair_placement_vars[(spec.event_id, slot_id)] for slot_id in slot_ids
            )


def add_room_exclusivity(model: cp_model.CpModel, onsite_room_choice_vars: dict) -> None:
    """В одной аудитории в одном слоте — не более одного очного занятия."""
    vars_by_slot_room: dict[tuple[int, int], list] = {}
    for (_event_id, slot_id, room_id), var in onsite_room_choice_vars.items():
        vars_by_slot_room.setdefault((slot_id, room_id), []).append(var)
    for slot_room_vars in vars_by_slot_room.values():
        if len(slot_room_vars) > 1:
            model.AddAtMostOne(slot_room_vars)


def add_attendee_exclusivity(model: cp_model.CpModel, specs: list[EventSpec],
                             slot_ids: list[int], pair_placement_vars: dict) -> None:
    """Учебная группа / студент: не более одного занятия (любого формата)
    в слоте. ПД блокирует только студентов проекта, практики/лекции —
    целиком учебные группы."""
    vars_by_slot_attendee: dict[tuple[str, int, int], list] = {}
    for spec in specs:
        for slot_id in slot_ids:
            placement_var = pair_placement_vars[(spec.event_id, slot_id)]
            for group_id in spec.blocking_group_ids:
                vars_by_slot_attendee.setdefault(
                    ("group", group_id, slot_id), []).append(placement_var)
            for student_id in spec.blocking_student_ids:
                vars_by_slot_attendee.setdefault(
                    ("student", student_id, slot_id), []).append(placement_var)
    for attendee_vars in vars_by_slot_attendee.values():
        if len(attendee_vars) > 1:
            model.AddAtMostOne(attendee_vars)


def add_teacher_exclusivity(model: cp_model.CpModel, specs: list[EventSpec],
                            slot_ids: list[int], pair_placement_vars: dict) -> None:
    """Преподаватель ведёт не более одного занятия в слоте
    (перегрузка по дням — мягкое ограничение, см. objectives.py)."""
    vars_by_teacher_slot: dict[int, dict[int, list]] = {}
    for spec in specs:
        if spec.teacher_id is None:
            continue
        for slot_id in slot_ids:
            vars_by_teacher_slot.setdefault(spec.teacher_id, {}).setdefault(
                slot_id, []).append(pair_placement_vars[(spec.event_id, slot_id)])
    for slot_map in vars_by_teacher_slot.values():
        for teacher_slot_vars in slot_map.values():
            if len(teacher_slot_vars) > 1:
                model.AddAtMostOne(teacher_slot_vars)


def add_day_restrictions(model: cp_model.CpModel, specs: list[EventSpec],
                         day_of_week_by_slot_id: dict[int, int],
                         pair_placement_vars: dict) -> None:
    """Событие можно ставить только в слоты разрешённых дней недели.

    Например, проектная деятельность занимает пн и ср, и в эти дни другие
    пары не планируются: у обычных занятий allowed_days = {0,1,3,4,5},
    у ПД allowed_days = {0,2} (см. services/day_rules.py и event_specs.py).
    allowed_days == frozenset() означает «без ограничений по дням».
    """
    for spec in specs:
        if not spec.allowed_days:
            continue
        for slot_id, day_of_week in day_of_week_by_slot_id.items():
            if day_of_week not in spec.allowed_days:
                model.Add(pair_placement_vars[(spec.event_id, slot_id)] == 0)


def _not_any(terms: list, model: cp_model.CpModel, name: str) -> cp_model.IntVar:
    """Булева переменная, равная 1 тогда и только тогда, когда все terms == 0."""
    empty_var = model.NewBoolVar(name)
    if not terms:
        model.Add(empty_var == 1)
    else:
        # пусто <=> ни один term не равен 1
        model.AddBoolOr([empty_var] + [term.Not() for term in terms])
        for term in terms:
            model.Add(empty_var == 0).OnlyEnforceIf(term)
    return empty_var


def add_lecture_onsite_gap(model: cp_model.CpModel, specs: list[EventSpec],
                          slot_ids: list[int], day_of_week_by_slot_id: dict[int, int],
                          pair_placement_vars: dict) -> None:
    """Между лекцией и очным занятием одного участника в один день — пропуск.

    Лекции онлайн, очные пары требуют присутствия в корпусе; если они стоят
    подряд без свободного слота между ними, у участника не остаётся времени
    ни на переход, ни на отдых. Правило предметной области: в один день
    между первой из них и второй обязательно хотя бы один пустой слот.

    Реализация для каждого участника (учебная группа / студент) и дня:
    - first_onsite == 1 <=> слот занят первым очным занятия дня (до него
      очных нет); last_onsite — симметрично для последних очных;
    - lectures_before = сумма x_лекц по слотам строго до s;
    - first_onsite + lectures_before <= 1  (перед первым очным лекций нет);
    - last_onsite + lectures_after <= 1    (после последнего очного лекций нет).
    Тогда лекция раньше всех очных => перед первым очным есть пустой слот;
    лекция позже всех очных => после последнего очного есть пустой слот;
    лекция между очными => соседние очные разделены пустым слотом.

    События с носителями разного вида (поток лекции блокирует группы, ПД —
    студентов) соединяются через переменные занятости пересечения:
    busy(g ∩ st, слот) == OR(x_события), где событие блокирует и g, и st.

    Ограничение добавляется только участникам, у которых есть и очное
    занятие, и лекция (иначе оно ложится на 0=0 и безвредно).
    """
    days_of_week = sorted(set(day_of_week_by_slot_id.values()))
    # slot_ids упорядочены по (day, lesson_number) — см. generator.py
    day_slot_ids = {
        day: [slot_id for slot_id in slot_ids if day_of_week_by_slot_id[slot_id] == day]
        for day in days_of_week
    }

    onsite_by_group: dict[int, dict[int, list]] = defaultdict(lambda: defaultdict(list))
    onsite_by_student: dict[int, dict[int, list]] = defaultdict(lambda: defaultdict(list))
    lecture_by_group: dict[int, dict[int, list]] = defaultdict(lambda: defaultdict(list))
    lecture_by_student: dict[int, dict[int, list]] = defaultdict(lambda: defaultdict(list))
    shared_pairs: set[tuple[int, int]] = set()  # (group_id, student_id)

    for spec in specs:
        is_lecture = spec.lesson_type == LessonType.LECTURE
        if not (is_lecture or spec.format == Format.ONSITE):
            continue  # онлайн-занятия не участвуют в правиле
        target = (lecture_by_group if is_lecture else onsite_by_group)
        for group_id in spec.blocking_group_ids:
            for slot_id in slot_ids:
                target[group_id][slot_id].append(pair_placement_vars[(spec.event_id, slot_id)])
        target = (lecture_by_student if is_lecture else onsite_by_student)
        for student_id in spec.blocking_student_ids:
            for slot_id in slot_ids:
                target[student_id][slot_id].append(pair_placement_vars[(spec.event_id, slot_id)])
        for group_id in spec.blocking_group_ids:
            for student_id in spec.blocking_student_ids:
                shared_pairs.add((group_id, student_id))

    def collect_terms(terms_by_slot: dict[int, list], day: int, start: int, end: int) -> list:
        terms = []
        for slot_id in day_slot_ids[day][start:end]:
            terms.extend(terms_by_slot.get(slot_id, []))
        return terms

    def apply_rule(onsite_terms_by_slot: dict[int, list],
                   lecture_terms_by_slot: dict[int, list],
                   participant_label: str) -> None:
        for day in days_of_week:
            slots = day_slot_ids[day]
            has_onsite = any(onsite_terms_by_slot.get(slot_id) for slot_id in slots)
            has_lecture = any(lecture_terms_by_slot.get(slot_id) for slot_id in slots)
            if not (has_onsite and has_lecture):
                continue
            for index, slot_id in enumerate(slots):
                onsite_here = onsite_terms_by_slot.get(slot_id, [])
                if not onsite_here:
                    continue
                prefix_onsite = collect_terms(onsite_terms_by_slot, day, 0, index)
                suffix_onsite = collect_terms(onsite_terms_by_slot, day, index + 1, len(slots))
                lectures_before = collect_terms(lecture_terms_by_slot, day, 0, index)
                lectures_after = collect_terms(lecture_terms_by_slot, day, index + 1, len(slots))

                onsite_here_bool = model.NewBoolVar(
                    f"onsh_{participant_label}_{day}_{index}")
                model.Add(onsite_here_bool == sum(onsite_here))

                first_onsite = model.NewBoolVar(
                    f"firsto_{participant_label}_{day}_{index}")
                # first_onsite == onsite_here_bool AND prefix_empty
                model.AddMultiplicationEquality(first_onsite, [
                    onsite_here_bool,
                    _not_any(prefix_onsite, model,
                             f"nopfx_{participant_label}_{day}_{index}")])
                if lectures_before:
                    model.Add(first_onsite + sum(lectures_before) <= 1)

                last_onsite = model.NewBoolVar(f"lasto_{participant_label}_{day}_{index}")
                suffix_empty = _not_any(suffix_onsite, model,
                                        f"nosfx_{participant_label}_{day}_{index}")
                model.AddMultiplicationEquality(last_onsite, [onsite_here_bool, suffix_empty])
                if lectures_after:
                    model.Add(last_onsite + sum(lectures_after) <= 1)

    for group_id, terms_by_slot in onsite_by_group.items():
        apply_rule(terms_by_slot, lecture_by_group[group_id], f"g{group_id}")
    for student_id, terms_by_slot in onsite_by_student.items():
        apply_rule(terms_by_slot, lecture_by_student[student_id], f"s{student_id}")

    # Пересечения «группа лекции ∩ студент ПД»: блокировки разных видов
    # соединяются переменной занятости слота на пересечении.
    for group_id, student_id in shared_pairs:
        label = f"gs{group_id}_{student_id}"
        merged_onsite: dict[int, list] = {}
        merged_lecture: dict[int, list] = {}
        for slot_id in slot_ids:
            for kind, group_terms_map, student_terms_map, merged in (
                    ("onsite", onsite_by_group[group_id], onsite_by_student[student_id],
                     merged_onsite),
                    ("lec", lecture_by_group[group_id], lecture_by_student[student_id],
                     merged_lecture)):
                group_terms = group_terms_map.get(slot_id, [])
                student_terms = student_terms_map.get(slot_id, [])
                if not (group_terms and student_terms):
                    continue
                overlap_var = model.NewBoolVar(f"ovl_{label}_{kind}_{slot_id}")
                model.AddMaxEquality(overlap_var, [
                    gp * st for gp in group_terms for st in student_terms])
                merged.setdefault(slot_id, []).append(overlap_var)
        if merged_onsite and merged_lecture:
            apply_rule(merged_onsite, merged_lecture, label)


def add_location_consistency(model: cp_model.CpModel, specs: list[EventSpec],
                              slot_ids: list[int], pair_placement_vars: dict,
                              onsite_room_choice_vars: dict,
                              location_id_by_room_id: dict[int, int]) -> None:
    """Один участник (группа/студент/преподаватель) не бывает в двух разных
    учебных локациях в одном слоте: между корпусами на разных локациях час езды.

    Реализация — зеркальные булевы переменные присутствия участника на
    локации в слоте:
        presence(participant, location, slot) == 1
            <=> participant ведёт/слушает очное событие на этой локации.
    - presence >= каждая room_choice этого сочетания;
    - presence <= сумма room_choice этого сочетания;
    - сумма presence по всем локациям для (participant, slot) <= 1.
    Онлайн-занятия аудитории не занимают и presence не создают.
    """
    participants_by_event: dict[int, list[tuple[str, int]]] = defaultdict(list)
    for spec in specs:
        if spec.format != Format.ONSITE:
            continue
        for group_id in spec.blocking_group_ids:
            participants_by_event[spec.event_id].append(("group", group_id))
        for student_id in spec.blocking_student_ids:
            participants_by_event[spec.event_id].append(("student", student_id))
        if spec.teacher_id is not None:
            participants_by_event[spec.event_id].append(("teacher", spec.teacher_id))

    # (participant, location, slot) -> room_choice-переменные этого сочетания
    choice_vars_by_presence: dict[tuple[tuple[str, int], int, int], list] = defaultdict(list)
    for (event_id, slot_id, room_id), choice_var in onsite_room_choice_vars.items():
        location_id = location_id_by_room_id.get(room_id)
        if location_id is None:
            continue  # аудитория без корпуса/локации — ограничение не применяется
        for participant in participants_by_event.get(event_id, ()):
            choice_vars_by_presence[(participant, location_id, slot_id)].append(choice_var)

    presence_vars_by_participant_slot: dict[tuple[tuple[str, int], int], list] = defaultdict(list)
    for (participant, location_id, slot_id), choice_vars \
            in choice_vars_by_presence.items():
        presence_var = model.NewBoolVar(
            f"pres_{participant[0]}{participant[1]}_l{location_id}_s{slot_id}")
        model.Add(presence_var >= sum(choice_vars))
        model.Add(presence_var <= sum(choice_vars))
        presence_vars_by_participant_slot[(participant, slot_id)].append(presence_var)

    for participant_slot_presences in presence_vars_by_participant_slot.values():
        if len(participant_slot_presences) > 1:
            model.AddAtMostOne(participant_slot_presences)


def add_event_pair_vars(model: cp_model.CpModel, specs: list[EventSpec],
                        slot_ids: list[int], pair_placement_vars: dict) -> dict[int, cp_model.IntVar]:
    """Для каждого события: placed_pairs[e] = число поставленных пар и
    missing_pairs[e] >= pairs_per_week - placed_pairs (недостающие пары).

    Возвращает {event_id: missing_pairs_var}; missing-переменные описывают
    неполностью запланированные события (используются целевой функцией).
    """
    missing_pairs_by_event: dict[int, cp_model.IntVar] = {}
    for spec in specs:
        placed_pairs = model.NewIntVar(0, spec.pairs_per_week,
                                       f"placed_{spec.event_id}")
        model.Add(placed_pairs == sum(
            pair_placement_vars[(spec.event_id, slot_id)] for slot_id in slot_ids))
        missing_pairs = model.NewIntVar(0, spec.pairs_per_week,
                                        f"missing_{spec.event_id}")
        model.Add(missing_pairs >= spec.pairs_per_week - placed_pairs)
        missing_pairs_by_event[spec.event_id] = missing_pairs
    return missing_pairs_by_event


def link_onsite_room_vars(model: cp_model.CpModel, specs: list[EventSpec],
                          slot_ids: list[int], pair_placement_vars: dict,
                          onsite_room_choice_vars: dict,
                          suitable_rooms_by_event_id: dict[int, list[int]]) -> None:
    """Связывает переменные размещения и выбора аудитории для очных событий:
    - если ни одна аудитория не подходит по вместимости — пары невозможны;
    - каждая поставленная очная пара получает ровно одну аудиторию."""
    for spec in specs:
        if spec.format != Format.ONSITE:
            continue
        suitable_room_ids = suitable_rooms_by_event_id.get(spec.event_id, [])
        if not suitable_room_ids:
            for slot_id in slot_ids:
                model.Add(pair_placement_vars[(spec.event_id, slot_id)] == 0)
        else:
            for slot_id in slot_ids:
                room_choice_vars = [
                    onsite_room_choice_vars[(spec.event_id, slot_id, room_id)]
                    for room_id in suitable_room_ids
                ]
                model.Add(sum(room_choice_vars) ==
                          pair_placement_vars[(spec.event_id, slot_id)])
