"""Модель генерации расписания на Google OR-Tools (CP-SAT).

Подход: недельное шаблонное расписание. Каждое LessonEvent требует
`pairs_per_week` пар в неделю; решается задача размещения этих пар по слотам
шаблона недели (день + номер пары) с выбором аудитории для очных занятий.

Учёт особенностей из предметной области:
- Практики/лабораторки (очные, на одну группу): жёсткие ограничения —
  группа не может быть в двух местах одновременно, аудитория одна на слот,
  вместимость >= размер группы.
- Лекции (онлайн, на поток групп): лекция одного потока идёт строго в один
  слот недели (все группы потока слушают её одновременно); группы-слушатели
  не могут пересекаться с другими занятиями в этом слоте; аудитория не нужна.
- Физкультура (PE): полностью исключается из модели — студент приходит
  когда и куда хочет (см. services/event_specs.py).
- ПД (очно, «группа» = студенты, выбравшие проект): блокируются конкретные
  студенты проекта (а не целиком учебные группы), чтобы студенты других
  учебных групп могли учиться в тот же слот; всегда очная — требует аудиторию.
- Внеучебка: может быть очной или онлайн (поле format на событии);
  очная занимает аудиторию, онлайн — нет. Тоже участвует в генерации.
- Дни ПД: проектная деятельность занимает понедельники и среды; в эти дни
  обычные пары (практики/лабы/лекции) не планируются (services/day_rules.py,
  add_day_restrictions).
- Локации: корпуса стоят на разных локациях, между каждым час езды,
  поэтому один участник (группа/студент/преподаватель) не может в одном
  слоте находиться на двух разных локациях (add_location_consistency);
  аудитории без корпуса в ограничении не участвуют. Дорога занимает больше
  перерыва, поэтому то же верно для соседних слотов одного дня
  (config.TRAVEL_BUFFER_SLOTS).

Soft-ограничения (штрафы в целевой функции, см. objectives.py):
- не более max_pairs_per_day у преподавателя в день;
- «окна» у групп (пропуск между занятыми слотами в течение дня);
- индекс усталости групп: превышение комфортного уровня и самый тяжёлый день;
- хронотип преподавателя (жаворонок/сова);
- режим подготовки к сессии: свободные дни у групп (PlanningPreferences).

Перепланирование: ``pinned`` закрепляет уже стоящие пары бонусом, ``forbidden``
запрещает отдельные размещения (см. services/replan.py).

Модель реализуема всегда: пара либо ставится, либо нет; недостающие пары
событий фиксируются как unscheduled (-> GenerationIssue, статус PARTIAL).

Структура модуля (порядок чтения = порядок сборки модели):
    solve() -> _build_variables -> hard (constraints.py)
            -> soft+objective (objectives.py) -> решение -> extract_result (solution.py)
"""

from __future__ import annotations

import time

from ortools.sat.python import cp_model

from ..enums import Format
from .config import (
    PENALTY_CHRONOTYPE,
    PENALTY_FATIGUE_EXCESS,
    PENALTY_FATIGUE_PEAK,
    PENALTY_FREE_DAY_VIOLATION,
    PIN_BONUS,
    PlanningPreferences,
    SolverConfig,
)
from .constraints import (
    add_attendee_exclusivity,
    add_day_restrictions,
    add_event_pair_vars,
    add_lecture_single_slot,
    add_location_consistency,
    add_room_exclusivity,
    add_teacher_exclusivity,
    link_onsite_room_vars,
)
from .dto import EventSpec, GenerationResult
from .objectives import (
    build_objective,
    collect_chronotype_penalties,
    collect_fatigue_penalties,
    collect_free_day_penalties,
    collect_teacher_overload_penalties,
    collect_window_penalties,
)
from .solution import extract_result


def generate_schedule(
    *,
    slots,
    rooms,
    specs: list[EventSpec],
    teachers_by_id: dict[int, dict],
    config: SolverConfig | None = None,
    preferences: PlanningPreferences | None = None,
    pinned: list[dict] | None = None,
    forbidden: set[tuple[int, int]] | None = None,
) -> GenerationResult:
    """Полный цикл решения: построить модель -> решить -> извлечь результат.

    :param slots: iterable TimeSlot (уже только нужного week_pattern)
    :param rooms: iterable Room (с preloaded building.location для правила локаций)
    :param specs: результат services/event_specs.build_event_specs
    :param teachers_by_id: {id: {"max_pairs_per_day": int, "chronotype": str}}
    :param config: параметры решателя (лимит времени, seed, потоки)
    :param preferences: режимы мягких правил (например, свободные дни)
    :param pinned: пары {event_id, slot_id, room_id}, которые желательно не
        двигать (перепланирование); мягко, через бонус целевой функции
    :param forbidden: размещения (event_id, slot_id), которые запрещены
    """
    solver_config = config or SolverConfig()
    start_time = time.time()

    model, model_context = _build_model(
        slots=slots, rooms=rooms, specs=specs, teachers_by_id=teachers_by_id,
        preferences=preferences or PlanningPreferences(),
        pinned=pinned or [], forbidden=forbidden or set())
    if model_context is None:  # пустой шаблон недели — решать нечего
        return GenerationResult("INFEASIBLE", None, time.time() - start_time, [],
                                [spec.event_id for spec in specs],
                                "Нет доступных слотов (шаблон недели пуст).")

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(solver_config.time_limit_seconds)
    solver.parameters.random_seed = solver_config.seed
    solver.parameters.num_search_workers = solver_config.num_search_workers
    status = solver.Solve(model)

    status_name = solver.StatusName(status)
    feasible = status in (cp_model.OPTIMAL, cp_model.FEASIBLE)

    result = extract_result(
        solver=solver, status_name=status_name, feasible=feasible,
        specs=specs, room_ids=model_context["room_ids"],
        pair_placement_vars=model_context["pair_placement_vars"],
        onsite_room_choice_vars=model_context["onsite_room_choice_vars"],
    )
    result.solve_time = time.time() - start_time
    return result


def _build_model(*, slots, rooms, specs: list[EventSpec],
                 teachers_by_id: dict[int, dict],
                 preferences: PlanningPreferences,
                 pinned: list[dict], forbidden: set[tuple[int, int]]):
    """Собирает CpModel по частям (hard -> soft -> objective).

    Возвращает (model, model_context); model_context — переменные, нужные для
    извлечения решения. model_context is None, если слотов нет вовсе
    (задача вырождена).
    """
    model = cp_model.CpModel()

    slot_list = sorted(slots, key=lambda slot: (slot.day_of_week,
                                                slot.lesson_number, slot.id))
    slot_ids = [slot.id for slot in slot_list]   # упорядочены по (day, lesson_number)
    day_of_week_by_slot_id = {slot.id: slot.day_of_week for slot in slot_list}
    lesson_number_by_slot_id = {slot.id: slot.lesson_number for slot in slot_list}
    room_ids = [room.id for room in rooms]
    capacity_by_room_id = {room.id: room.capacity for room in rooms}
    # локация аудитории; если у аудитории нет корпуса/локации —
    # ограничение «одна локация на слот» для неё не применяется
    location_id_by_room_id = {
        room.id: location_id
        for room in rooms
        if room.building_id and (location_id := room.building.location_id) is not None
    }

    if not slot_ids:
        return model, None

    # ---- переменные ----
    pair_placement_vars, onsite_room_choice_vars, suitable_rooms_by_event_id = \
        _build_variables(model, specs, slot_ids, room_ids, capacity_by_room_id)

    # ---- жёсткие ограничения (services/constraints.py) ----
    add_lecture_single_slot(model, specs, slot_ids, pair_placement_vars)
    add_room_exclusivity(model, onsite_room_choice_vars)
    add_attendee_exclusivity(model, specs, slot_ids, pair_placement_vars)
    add_teacher_exclusivity(model, specs, slot_ids, pair_placement_vars)
    add_day_restrictions(model, specs, day_of_week_by_slot_id, pair_placement_vars)
    add_location_consistency(model, specs, slot_ids, pair_placement_vars,
                             onsite_room_choice_vars, location_id_by_room_id,
                             day_of_week_by_slot_id, lesson_number_by_slot_id)
    add_event_pair_vars(model, specs, slot_ids, pair_placement_vars)
    link_onsite_room_vars(model, specs, slot_ids, pair_placement_vars,
                          onsite_room_choice_vars, suitable_rooms_by_event_id)
    for event_id, slot_id in forbidden:
        if (event_id, slot_id) in pair_placement_vars:
            model.Add(pair_placement_vars[(event_id, slot_id)] == 0)

    # ---- мягкие ограничения и целевая функция (services/objectives.py) ----
    teacher_overload_vars = collect_teacher_overload_penalties(
        model, specs, day_of_week_by_slot_id, pair_placement_vars, teachers_by_id)
    window_vars = collect_window_penalties(
        model, specs, slot_ids, day_of_week_by_slot_id, pair_placement_vars)
    fatigue_excess_vars, fatigue_peak_vars = collect_fatigue_penalties(
        model, specs, slot_ids, day_of_week_by_slot_id, lesson_number_by_slot_id,
        pair_placement_vars)
    chronotype_vars = collect_chronotype_penalties(
        specs, slot_ids, lesson_number_by_slot_id, pair_placement_vars, teachers_by_id)
    free_day_violation_vars = collect_free_day_penalties(
        model, specs, slot_ids, day_of_week_by_slot_id, pair_placement_vars,
        preferences.free_days_per_group)
    pinned_vars = _pinned_vars(pinned, pair_placement_vars, onsite_room_choice_vars)
    build_objective(
        model, specs, slot_ids, pair_placement_vars, window_vars, teacher_overload_vars,
        extra_penalties=[
            (PENALTY_FATIGUE_EXCESS, fatigue_excess_vars),
            (PENALTY_FATIGUE_PEAK, fatigue_peak_vars),
            (PENALTY_CHRONOTYPE, chronotype_vars),
            (PENALTY_FREE_DAY_VIOLATION, free_day_violation_vars),
        ],
        extra_rewards=[(PIN_BONUS, pinned_vars)],
    )

    return model, {
        "pair_placement_vars": pair_placement_vars,
        "onsite_room_choice_vars": onsite_room_choice_vars,
        "room_ids": room_ids,
    }


def _pinned_vars(pinned: list[dict], pair_placement_vars: dict,
                 onsite_room_choice_vars: dict) -> list:
    """Переменные закреплённых пар: размещение в том же слоте и, если известна,
    в той же аудитории. Пары, которых нет в модели, пропускаются."""
    pinned_vars = []
    for assignment in pinned:
        placement_key = (assignment["event_id"], assignment["slot_id"])
        if placement_key in pair_placement_vars:
            pinned_vars.append(pair_placement_vars[placement_key])
        room_key = (*placement_key, assignment.get("room_id"))
        if room_key in onsite_room_choice_vars:
            pinned_vars.append(onsite_room_choice_vars[room_key])
    return pinned_vars


def _build_variables(model: cp_model.CpModel, specs: list[EventSpec],
                     slot_ids: list[int], room_ids: list[int],
                     capacity_by_room_id: dict[int, int]):
    """Создаёт переменные модели.

    pair_placement_vars[event_id, slot_id] — ставить ли пару события в слот;
    onsite_room_choice_vars[event_id, slot_id, room_id] — очная пара события
        в слоте в данной аудитории
        (только для очных событий и подходящих по вместимости аудиторий).
    """
    pair_placement_vars = {}
    for spec in specs:
        for slot_id in slot_ids:
            pair_placement_vars[(spec.event_id, slot_id)] = model.NewBoolVar(
                f"place_{spec.event_id}_{slot_id}"
            )

    onsite_room_choice_vars = {}
    suitable_rooms_by_event_id: dict[int, list[int]] = {}   # event_id -> аудитории
    for spec in specs:
        if spec.format != Format.ONSITE:
            continue
        suitable_room_ids = [
            room_id for room_id in room_ids
            if capacity_by_room_id[room_id] >= spec.audience_size
        ]
        suitable_rooms_by_event_id[spec.event_id] = suitable_room_ids
        for slot_id in slot_ids:
            for room_id in suitable_room_ids:
                onsite_room_choice_vars[(spec.event_id, slot_id, room_id)] = \
                    model.NewBoolVar(f"room_{spec.event_id}_{slot_id}_{room_id}")
    return pair_placement_vars, onsite_room_choice_vars, suitable_rooms_by_event_id
