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

from ..enums import Format
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


def add_lecture_not_adjacent(model: cp_model.CpModel, specs: list[EventSpec],
                             slot_ids: list[int],
                             day_of_week_by_slot_id: dict[int, int],
                             pair_placement_vars: dict) -> None:
    """Лекции слушателей не могут идти подряд: между лекцией и предыдущей или
    следующей парой того же дня должен быть пропуск (свободный слот).

    Лекцию можно ставить в любой день недели (кроме воскресенья — см.
    day_rules.LECTURE_ALLOWED_DAYS), включая дни ПД; но соседний по номеру
    пары слот того же дня блокируется для всех групп-слушателей потока, если
    в нём есть любое другое занятие этих групп. Ограничение симметричное:
    лекция запрещена как сразу после, так и сразу перед занятием группы.

    Реализация: для каждой группы g и слота s (день d, номер k) вводится
    вспомогательная булева переменная
        other_{g,s} = 1 <=> в слоте s стоит хотя бы одно занятие группы g,
                            КРОМЕ лекций, которым разрешён этот слот
                            (чтобы лекция потока не блокировала саму себя);
    затем для каждой лекции L потока, куда входит g, и каждого соседа s'
    слота s того же дня:
        place[L, s'] + other_{g,s} <= 1.
    Занятия, которые и так не могут стоять в слоте s (например, обычные пары
    в пн/ср — дни ПД), в other не входят: иначе запрет ложно срабатывал бы
    относительно слотов, всегда пустых для этой группы.
    """
    lecture_specs = [spec for spec in specs if spec.stream_id is not None]
    if not lecture_specs:
        return

    # слоты одного дня, упорядоченные по номеру пары
    # (slot_ids уже отсорированы по (day, lesson_number) — см. generator.py)
    slot_ids_by_day: dict[int, list[int]] = defaultdict(list)
    for slot_id in slot_ids:
        slot_ids_by_day[day_of_week_by_slot_id[slot_id]].append(slot_id)

    # лекции, которым разрешён слот по правилам дней: они исключаются из
    # «других занятий», чтобы лекция не создавала запрет против самой себя
    lectures_allowed_in_slot: dict[int, set[int]] = defaultdict(set)
    for spec in lecture_specs:
        for slot_id in slot_ids:
            if (not spec.allowed_days
                    or day_of_week_by_slot_id[slot_id] in spec.allowed_days):
                lectures_allowed_in_slot[slot_id].add(spec.event_id)

    # слагаемые «занятие группы g в слоте s» (кроме разрешённых здесь лекций)
    other_terms_by_group_slot: dict[tuple[int, int], list] = defaultdict(list)
    for spec in specs:
        for slot_id in slot_ids:
            if spec.event_id in lectures_allowed_in_slot[slot_id]:
                continue
            for group_id in spec.blocking_group_ids:
                other_terms_by_group_slot[(group_id, slot_id)].append(
                    pair_placement_vars[(spec.event_id, slot_id)])

    # other-переменная создаётся один раз на (группа, слот) и переиспользуется
    other_var_by_group_slot: dict[tuple[int, int], cp_model.IntVar] = {}
    for day, day_slot_ids in slot_ids_by_day.items():
        for index, slot_id in enumerate(day_slot_ids):
            neighbor_slot_ids = []
            if index > 0:
                neighbor_slot_ids.append(day_slot_ids[index - 1])
            if index + 1 < len(day_slot_ids):
                neighbor_slot_ids.append(day_slot_ids[index + 1])
            if not neighbor_slot_ids:
                continue
            for spec in lecture_specs:
                for group_id in spec.blocking_group_ids:
                    key = (group_id, slot_id)
                    other_terms = other_terms_by_group_slot[key]
                    if not other_terms:
                        continue  # в слоте группе ставить нечего — пропуск гарантирован
                    other_var = other_var_by_group_slot.get(key)
                    if other_var is None:
                        other_var = model.NewBoolVar(f"other_{group_id}_{slot_id}")
                        model.Add(other_var == sum(other_terms))
                        other_var_by_group_slot[key] = other_var
                    for neighbor_slot_id in neighbor_slot_ids:
                        model.Add(pair_placement_vars[(spec.event_id,
                                                       neighbor_slot_id)]
                                  + other_var <= 1)


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


def add_campus_consistency(model: cp_model.CpModel, specs: list[EventSpec],
                           slot_ids: list[int], pair_placement_vars: dict,
                           onsite_room_choice_vars: dict,
                           campus_id_by_room_id: dict[int, int]) -> None:
    """Один участник (группа/студент/преподаватель) не бывает в двух разных
    учебных площадках (кампусах) в одном слоте: площадки далеко друг от друга.

    Реализация — зеркальные булевы переменные присутствия участника на
    площадке в слоте:
        presence(participant, campus, slot) == 1
            <=> participant ведёт/слушает очное событие на этой площадке.
    - presence >= каждая room_choice этого сочетания;
    - presence <= сумма room_choice этого сочетания;
    - сумма presence по всем площадкам для (participant, slot) <= 1.
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

    # (participant, campus, slot) -> room_choice-переменные этого сочетания
    choice_vars_by_presence: dict[tuple[tuple[str, int], int, int], list] = defaultdict(list)
    for (event_id, slot_id, room_id), choice_var in onsite_room_choice_vars.items():
        campus_id = campus_id_by_room_id.get(room_id)
        if campus_id is None:
            continue  # аудитория без корпуса — ограничение не применяется
        for participant in participants_by_event.get(event_id, ()):
            choice_vars_by_presence[(participant, campus_id, slot_id)].append(choice_var)

    presence_vars_by_participant_slot: dict[tuple[tuple[str, int], int], list] = defaultdict(list)
    for (participant, campus_id, slot_id), choice_vars \
            in choice_vars_by_presence.items():
        presence_var = model.NewBoolVar(
            f"pres_{participant[0]}{participant[1]}_c{campus_id}_s{slot_id}")
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
