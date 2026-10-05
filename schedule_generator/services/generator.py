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
    :param teachers_by_id: {id: {"max_per_day": int}}
    :param config: параметры решателя (лимит времени, seed, потоки)
    """
    cfg = config or SolverConfig()
    t0 = time.time()

    model, ctx = _build_model(slots=slots, rooms=rooms, specs=specs,
                              teachers_by_id=teachers_by_id)
    if ctx is None:  # пустой шаблон недели — решать нечего
        return GenerationResult("INFEASIBLE", None, time.time() - t0, [],
                                [sp.event_id for sp in specs],
                                "Нет доступных слотов (шаблон недели пуст).")

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(cfg.time_limit_seconds)
    solver.parameters.random_seed = cfg.seed
    solver.parameters.num_search_workers = cfg.num_search_workers
    status = solver.Solve(model)

    status_name = solver.StatusName(status)
    feasible = status in (cp_model.OPTIMAL, cp_model.FEASIBLE)

    result = extract_result(solver=solver, status_name=status_name, feasible=feasible,
                            specs=specs, room_ids=ctx["room_ids"],
                            x=ctx["x"], y=ctx["y"])
    result.solve_time = time.time() - t0
    return result


def _build_model(*, slots, rooms, specs: list[EventSpec],
                 teachers_by_id: dict[int, dict]):
    """Собирает CpModel по частям (hard -> soft -> objective).

    Возвращает (model, ctx); ctx — переменные, нужные для извлечения решения.
    ctx is None, если слотов нет вовсе (задача вырождена).
    """
    model = cp_model.CpModel()

    slot_list = sorted(slots, key=lambda s: (s.day_of_week, s.lesson_number, s.id))
    slot_ids = [s.id for s in slot_list]          # упорядочены по (day, lesson_number)
    slot_day = {s.id: s.day_of_week for s in slot_list}
    room_ids = [r.id for r in rooms]
    room_cap = {r.id: r.capacity for r in rooms}

    if not slot_ids:
        return model, None

    # ---- переменные ----
    x, y, onsite_ok_rooms = _build_variables(model, specs, slot_ids, room_ids, room_cap)

    # ---- жёсткие ограничения (services/constraints.py) ----
    add_lecture_single_slot(model, specs, slot_ids, x)
    add_room_exclusivity(model, y)
    add_attendee_exclusivity(model, specs, slot_ids, x)
    add_teacher_exclusivity(model, specs, slot_ids, x)
    add_event_pair_vars(model, specs, slot_ids, x)
    link_onsite_room_vars(model, specs, slot_ids, x, y, onsite_ok_rooms)

    # ---- мягкие ограничения и целевая функция (services/objectives.py) ----
    teacher_over_vars = collect_teacher_overload_penalties(
        model, specs, slot_day, x, teachers_by_id)
    window_vars = collect_window_penalties(model, specs, slot_ids, slot_day, x)
    build_objective(model, specs, slot_ids, x, window_vars, teacher_over_vars)

    return model, {"x": x, "y": y, "room_ids": room_ids}


def _build_variables(model: cp_model.CpModel, specs: list[EventSpec],
                     slot_ids: list[int], room_ids: list[int],
                     room_cap: dict[int, int]):
    """Создаёт переменные модели.

    x[e, s] — ставить ли пару события e в слот s;
    y[e, s, r] — очная пара события e в слоте s в аудитории r
                 (только для очных событий и подходящих по вместимости аудиторий).
    """
    x = {}
    for sp in specs:
        for sid in slot_ids:
            x[(sp.event_id, sid)] = model.NewBoolVar(f"x_{sp.event_id}_{sid}")

    y = {}
    onsite_ok_rooms: dict[int, list[int]] = {}   # event_id -> подходящие аудитории
    for sp in specs:
        if sp.fmt != Format.ONSITE:
            continue
        ok_rooms = [rid for rid in room_ids if room_cap[rid] >= sp.audience_size]
        onsite_ok_rooms[sp.event_id] = ok_rooms
        for sid in slot_ids:
            for rid in ok_rooms:
                y[(sp.event_id, sid, rid)] = model.NewBoolVar(
                    f"y_{sp.event_id}_{sid}_{rid}"
                )
    return x, y, onsite_ok_rooms
