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

Soft-ограничения (штрафы в целевой функции, см. objectives.py):
- не более max_pairs_per_day у преподавателя в день;
- «окна» у групп (пропуск между занятыми слотами в течение дня).

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
from .config import SolverConfig
from .constraints import (
    add_attendee_exclusivity,
    add_event_pair_vars,
    add_lecture_single_slot,
    add_room_exclusivity,
    add_teacher_exclusivity,
    link_onsite_room_vars,
)
from .dto import EventSpec, GenerationResult
from .objectives import (
    build_objective,
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
) -> GenerationResult:
    """Полный цикл решения: построить модель -> решить -> извлечь результат.

    :param slots: iterable TimeSlot (уже только нужного week_pattern)
    :param rooms: iterable Room
    :param specs: результат services/event_specs.build_event_specs
    :param teachers_by_id: {id: {"max_pairs_per_day": int}}
    :param config: параметры решателя (лимит времени, seed, потоки)
    """
    solver_config = config or SolverConfig()
    start_time = time.time()

    model, model_context = _build_model(slots=slots, rooms=rooms, specs=specs,
                                        teachers_by_id=teachers_by_id)
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
                 teachers_by_id: dict[int, dict]):
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
    room_ids = [room.id for room in rooms]
    capacity_by_room_id = {room.id: room.capacity for room in rooms}

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
    add_event_pair_vars(model, specs, slot_ids, pair_placement_vars)
    link_onsite_room_vars(model, specs, slot_ids, pair_placement_vars,
                          onsite_room_choice_vars, suitable_rooms_by_event_id)

    # ---- мягкие ограничения и целевая функция (services/objectives.py) ----
    teacher_overload_vars = collect_teacher_overload_penalties(
        model, specs, day_of_week_by_slot_id, pair_placement_vars, teachers_by_id)
    window_vars = collect_window_penalties(
        model, specs, slot_ids, day_of_week_by_slot_id, pair_placement_vars)
    build_objective(model, specs, slot_ids, pair_placement_vars,
                    window_vars, teacher_overload_vars)

    return model, {
        "pair_placement_vars": pair_placement_vars,
        "onsite_room_choice_vars": onsite_room_choice_vars,
        "room_ids": room_ids,
    }


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
