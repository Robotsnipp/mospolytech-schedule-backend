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


def add_lecture_onsite_gap(model: cp_model.CpModel, specs: list[EventSpec],
                           day_of_week_by_slot_id: dict[int, int],
                           pair_placement_vars: dict) -> None:
    """Жёсткое правило: лекция и очное занятие одного участника в один день
    не могут идти подряд — между ними обязана быть как минимум одна пустая
    пара (пропуск).

    Соседние слоты считаются внутри дня (см. slot_ids в generator.py —
    слоты упорядочены по (day_of_week, lesson_number)). Для каждой пары
    соседних слотов одного дня запрещаем одновременное размещение лекции
    в первый слот и очного занятия во второй, и наоборот.

    Участник: учебная группа / студент (слушатели), преподаватель (ведущий).
    Онлайн-лекция аудиторию не занимает, но у участников она всё равно
    блокирует слот; очные занятия — практики/лабы/ПД/очная внеучебка/ДПО.
    """
    lecture_specs = [spec for spec in specs if spec.lesson_type == LessonType.LECTURE]
    onsite_specs = [spec for spec in specs if spec.format == Format.ONSITE]
    if not lecture_specs or not onsite_specs:
        return

    # участники каждого события: группы/студенты-слушатели + преподаватель
    def participants_of(spec: EventSpec) -> set[tuple[str, int]]:
        result = {("group", group_id) for group_id in spec.blocking_group_ids}
        result |= {("student", student_id)
                   for student_id in spec.blocking_student_ids}
        if spec.teacher_id is not None:
            result.add(("teacher", spec.teacher_id))
        return result

    onsites_by_participant: dict[tuple[str, int], list[EventSpec]] = defaultdict(list)
    for spec in onsite_specs:
        for participant in participants_of(spec):
            onsites_by_participant[participant].append(spec)

    # соседние пары слотов одного дня: [(slot_a, slot_b), ...]
    slots_by_day: dict[int, list[int]] = defaultdict(list)
    for slot_id in sorted(day_of_week_by_slot_id, key=lambda sid: (
            day_of_week_by_slot_id[sid], sid)):
        slots_by_day[day_of_week_by_slot_id[slot_id]].append(slot_id)
    adjacent_pairs = [
        (day_slots[index], day_slots[index + 1])
        for day_slots in slots_by_day.values()
        for index in range(len(day_slots) - 1)
    ]

    for lecture in lecture_specs:
        lecture_participants = participants_of(lecture)
        shared_onsites: list[EventSpec] = []
        for participant in lecture_participants:
            for onsite in onsites_by_participant.get(participant, ()):
                if not any(onsite is other for other in shared_onsites):
                    shared_onsites.append(onsite)
        if not shared_onsites:
            continue
        for slot_a, slot_b in adjacent_pairs:
            lecture_var_a = pair_placement_vars[(lecture.event_id, slot_a)]
            lecture_var_b = pair_placement_vars[(lecture.event_id, slot_b)]
            for onsite in shared_onsites:
                # лекция раньше, очное сразу после неё — запрещено
                model.Add(lecture_var_a + pair_placement_vars[
                    (onsite.event_id, slot_b)] <= 1)
                # очное раньше, лекция сразу после него — запрещено
                model.Add(pair_placement_vars[
                    (onsite.event_id, slot_a)] + lecture_var_b <= 1)


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
