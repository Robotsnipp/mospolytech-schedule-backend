"""Генерация расписания на Google OR-Tools (CP-SAT).

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
  когда и куда хочет.
- ПД (очно, «группа» = студенты, выбравшие проект): блокируются конкретные
  студенты проекта (а не целиком учебные группы), чтобы студенты других
  учебных групп могли учиться в тот же слот; всегда очная — требует аудиторию.
- Внеучебка: может быть очной или онлайн (поле format на событии);
  очная занимает аудиторию, онлайн — нет. Тоже участвует в генерации.

Soft-ограничения (штрафы в целевой функции):
- не более max_pairs_per_day у преподавателя в день;
- «окна» у групп (пропуск между занятыми слотами в течение дня).

Модель реализуема всегда: пара либо ставится, либо нет; недостающие пары
событий фиксируются как unscheduled (-> GenerationIssue, статус PARTIAL).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from ortools.sat.python import cp_model


@dataclass
class EventSpec:
    """Подготовленные данные одного события для модели."""

    event_id: int
    lesson_type: str
    fmt: str                      # ONSITE | ONLINE
    pairs_per_week: int
    audience_size: int
    teacher_id: int | None
    # Учебные группы, целиком блокируемые занятием в слоте
    # (практика/лаборатория на группу, все группы-слушатели лекции).
    blocking_group_ids: set[int] = field(default_factory=set)
    # Отдельные студенты, блокируемые занятием (ПД: только выбравшие проект;
    # внеучебка на студентов — при необходимости).
    blocking_student_ids: set[int] = field(default_factory=set)
    # Для лекций: id потока — все его группы обязаны слушать в одном слоте
    stream_id: int | None = None


@dataclass
class GenerationResult:
    status: str
    objective_value: int | None
    solve_time: float
    assignments: list[dict]        # {event_id, slot_id, room_id|None}
    unscheduled_event_ids: list[int]
    message: str = ""


def build_event_specs(events):
    """Конвертирует queryset LessonEvent в specs, отбрасывая негенерируемые
    типы (физкультура) и события без корректного носителя.

    Возвращает (specs, skipped), где skipped — список пар
    (event, причина пропуска).
    """
    from .enums import NON_GENERATED_LESSON_TYPES, Format, LessonType
    from .models import Student

    specs: list[EventSpec] = []
    skipped = []
    for ev in events.select_related("group", "stream", "project", "teacher"):
        if ev.lesson_type in NON_GENERATED_LESSON_TYPES:
            # физкультура не генерируется: студент приходит в любое
            # время в любое подходящее место — это не событие расписания.
            continue

        fmt = ev.effective_format
        blocking: set[int] = set()
        blocking_students: set[int] = set()
        stream_id = None

        if ev.lesson_type == LessonType.LECTURE:
            if not ev.stream_id:
                skipped.append((ev, "Лекция без лекционного потока."))
                continue
            stream_id = ev.stream_id
            # все группы потока слушают лекцию -> они заблокированы
            blocking = set(ev.stream.groups.values_list("id", flat=True))
            audience = ev.audience_size or sum(
                g.students.count() for g in ev.stream.groups.all()
            )
        elif ev.project_id:
            if ev.lesson_type != LessonType.PD:
                skipped.append((ev, "Событие с проектом должно иметь тип ПД."))
                continue
            # ПД: группа проекта = студенты, выбравшие проект.
            # Блокируем именно этих студентов (а не целиком их учебные
            # группы), чтобы остальные студенты тех же групп могли учиться.
            blocking_students = set(
                Student.objects.filter(project_choice__project_id=ev.project_id)
                .values_list("id", flat=True)
            )
            audience = ev.audience_size or len(blocking_students)
            if not blocking_students:
                # никто не выбрал проект — планировать не для кого
                skipped.append((ev, "По проекту нет выбравших его студентов."))
                continue
        elif ev.group_id:
            blocking = {ev.group_id}
            audience = ev.audience_size
        else:
            skipped.append((ev, "У занятия нет носителя (группы/потока/проекта)."))
            continue

        specs.append(EventSpec(
            event_id=ev.id,
            lesson_type=ev.lesson_type,
            fmt=fmt,
            pairs_per_week=ev.pairs_per_week,
            audience_size=max(audience, 1),
            teacher_id=ev.teacher_id,
            blocking_group_ids=blocking,
            blocking_student_ids=blocking_students,
            stream_id=stream_id,
        ))
    return specs, skipped


def generate_schedule(
    *,
    slots,
    rooms,
    specs: list[EventSpec],
    teachers_by_id: dict[int, dict],
    time_limit_seconds: int = 30,
    seed: int = 17,
) -> GenerationResult:
    """Решает задачу размещения пар по слотам шаблона недели.

    :param slots: iterable TimeSlot (уже только нужного week_pattern)
    :param rooms: iterable Room
    :param specs: результат build_event_specs
    :param teachers_by_id: {id: {"max_per_day": int}}
    """
    t0 = time.time()
    from .enums import Format, LessonType

    model = cp_model.CpModel()

    slot_list = sorted(slots, key=lambda s: (s.day_of_week, s.lesson_number, s.id))
    slot_ids = [s.id for s in slot_list]
    slot_day = {s.id: s.day_of_week for s in slot_list}
    room_ids = [r.id for r in rooms]
    room_cap = {r.id: r.capacity for r in rooms}

    n_slots = len(slot_ids)
    if n_slots == 0:
        return GenerationResult("INFEASIBLE", None, time.time() - t0, [],
                                [sp.event_id for sp in specs],
                                "Нет доступных слотов (шаблон недели пуст).")

    # ---- переменные: x[e, s] — ставить ли пару события e в слот s ----
    x = {}
    for sp in specs:
        for sid in slot_ids:
            x[(sp.event_id, sid)] = model.NewBoolVar(f"x_{sp.event_id}_{sid}")

    # ---- переменные: y[e, s, r] — очная пара события e в слоте s в ауд. r ----
    # Создаём только для очных событий и подходящих по вместимости аудиторий.
    y = {}
    onsite_ok_rooms = {}   # event_id -> [rid, ...] подходящие аудитории
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

    # ---- лекция одного потока идёт в один слот на всю неделю ----
    # (все группы потока слушают её одновременно; аудитория не нужна)
    for sp in specs:
        if sp.stream_id is not None:
            model.AddAtMostOne(x[(sp.event_id, sid)] for sid in slot_ids)

    # ---- жёсткие ограничения "не более одного занятия" ----
    # 1) Аудитория: не более одного ОЧНОГО занятия в слоте.
    by_slot_room: dict[tuple[int, int], list] = {}
    for (eid, sid, rid), v in y.items():
        by_slot_room.setdefault((sid, rid), []).append(v)
    for (_sid, _rid), vs in by_slot_room.items():
        if len(vs) > 1:
            model.AddAtMostOne(vs)

    # 2) Учебная группа / студент: не более одного занятия (любого формата)
    #    в слоте. ПД блокирует только студентов проекта, практики/лекции —
    #    целиком учебные группы.
    by_slot_attendee: dict[tuple[str, int, int], list] = {}
    for sp in specs:
        for sid in slot_ids:
            for gid in sp.blocking_group_ids:
                by_slot_attendee.setdefault(("g", gid, sid), []).append(
                    x[(sp.event_id, sid)]
                )
            for stid in sp.blocking_student_ids:
                by_slot_attendee.setdefault(("s", stid, sid), []).append(
                    x[(sp.event_id, sid)]
                )
    for _key, vs in by_slot_attendee.items():
        if len(vs) > 1:
            model.AddAtMostOne(vs)

    # 3) Преподаватель: не более одного занятия в слоте; soft — max в день.
    by_slot_teacher: dict[int, dict[int, list]] = {}
    per_teacher_day: dict[tuple[int, int], list] = {}
    teacher_over_vars: list = []
    for sp in specs:
        if sp.teacher_id is None:
            continue
        for sid in slot_ids:
            by_slot_teacher.setdefault(sp.teacher_id, {}).setdefault(sid, []).append(
                x[(sp.event_id, sid)]
            )
            per_teacher_day.setdefault((sp.teacher_id, slot_day[sid]), []).append(
                x[(sp.event_id, sid)]
            )
    for tid, slot_map in by_slot_teacher.items():
        for sid, vs in slot_map.items():
            if len(vs) > 1:
                model.AddAtMostOne(vs)

    # ---- связь x <-> y и недостающие пары (soft) ----
    short_vars = []
    for sp in specs:
        xs = [x[(sp.event_id, sid)] for sid in slot_ids]
        placed = model.NewIntVar(0, sp.pairs_per_week, f"placed_{sp.event_id}")
        model.Add(placed == sum(xs))
        # недостающие обязательные пары (ставятся в штраф целевой функции)
        short = model.NewIntVar(0, sp.pairs_per_week, f"short_{sp.event_id}")
        model.Add(short >= sp.pairs_per_week - placed)
        short_vars.append(short)

        if sp.fmt == Format.ONSITE:
            ok_rooms = onsite_ok_rooms.get(sp.event_id, [])
            if not ok_rooms:
                # ни одна аудитория не подходит по вместимости —
                # такие пары невозможны; событие попадёт в unscheduled.
                for sid in slot_ids:
                    model.Add(x[(sp.event_id, sid)] == 0)
            else:
                # каждая поставленная очная пара получает ровно одну аудиторию
                for sid in slot_ids:
                    pair_y = [y[(sp.event_id, sid, rid)] for rid in ok_rooms]
                    model.Add(sum(pair_y) == x[(sp.event_id, sid)])

    for (tid, day), vs in per_teacher_day.items():
        tconf = teachers_by_id.get(tid, {})
        max_per_day = tconf.get("max_per_day")
        if max_per_day and len(vs) > max_per_day:
            over = model.NewIntVar(0, len(vs), f"tover_{tid}_{day}")
            model.Add(over >= sum(vs) - max_per_day)
            teacher_over_vars.append(over)

    # 4) Soft: «окна» у групп (для занятий на группы) и у студентов
    #    (для ПД) — штраф за пропущенный слот между двумя занятыми в день.
    win_penalty_vars = []
    days = sorted(set(slot_day.values()))
    slots_by_day = {d: [sid for sid in slot_ids if slot_day[sid] == d] for d in days}
    # slot_ids уже упорядочены по (day, lesson_number) — см. slot_list выше.

    occ_by_attendee: dict[tuple[str, int], dict[int, list]] = {}
    for sp in specs:
        for sid in slot_ids:
            term = x[(sp.event_id, sid)]
            for gid in sp.blocking_group_ids:
                occ_by_attendee.setdefault(("g", gid), {}).setdefault(sid, []).append(term)
            for stid in sp.blocking_student_ids:
                occ_by_attendee.setdefault(("s", stid), {}).setdefault(sid, []).append(term)

    for kind_id, occ in occ_by_attendee.items():
        for d in days:
            day_slots = slots_by_day[d]
            busy = []
            for i, sid in enumerate(day_slots):
                b = model.NewBoolVar(f"busy_{kind_id[0]}{kind_id[1]}_{d}_{i}")
                terms = occ.get(sid, [])
                if terms:
                    model.AddMaxEquality(b, terms)
                else:
                    model.Add(b == 0)
                busy.append(b)
            # окно: busy[i]==1 и busy[j]==1 (j>i+1), всё между пусто
            for i in range(len(busy)):
                for j in range(i + 2, len(busy)):
                    gap = model.NewBoolVar(f"gap_{kind_id[0]}{kind_id[1]}_{d}_{i}_{j}")
                    mid = busy[i + 1:j]
                    # gap <= busy_i, gap <= busy_j, gap <= 1 - mid_k (все k)
                    model.Add(gap <= busy[i])
                    model.Add(gap <= busy[j])
                    for m in mid:
                        model.Add(gap <= 1 - m)
                    win_penalty_vars.append(gap)

    # 5) Целевая функция: максимизировать число поставленных пар и
    #    минимизировать окна/перегрузку преподавателей.
    #    (short_vars не входят в целевую: placed == sum(x), поэтому штраф за
    #     недостающие пары эквивалентен награде за поставленные.)
    reward_placed = []
    for sp in specs:
        weight = 100
        if sp.lesson_type == LessonType.LECTURE:
            weight = 150   # лекции важны: их слушает много групп
        for sid in slot_ids:
            reward_placed.append(weight * x[(sp.event_id, sid)])

    window_weight = 3
    teacher_over_weight = 5
    model.Maximize(
        sum(reward_placed)
        - window_weight * sum(win_penalty_vars)
        - teacher_over_weight * sum(teacher_over_vars)
    )

    # ---- решение ----
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(time_limit_seconds)
    solver.parameters.random_seed = seed
    solver.parameters.num_search_workers = 8
    status = solver.Solve(model)

    status_name = solver.StatusName(status)
    feasible = status in (cp_model.OPTIMAL, cp_model.FEASIBLE)

    assignments = []
    unscheduled = []
    if feasible:
        for sp in specs:
            placed_any = False
            for sid in slot_ids:
                if solver.Value(x[(sp.event_id, sid)]):
                    room_id = None
                    if sp.fmt == Format.ONSITE:
                        for rid in room_ids:
                            key = (sp.event_id, sid, rid)
                            if key in y and solver.Value(y[key]):
                                room_id = rid
                                break
                    assignments.append({
                        "event_id": sp.event_id, "slot_id": sid, "room_id": room_id,
                    })
                    placed_any = True
            # проверка: поставлено ли хотя бы нужное число пар
            n_placed = sum(1 for a in assignments if a["event_id"] == sp.event_id)
            if n_placed < sp.pairs_per_week:
                unscheduled.append(sp.event_id)
    else:
        unscheduled = [sp.event_id for sp in specs]

    msg = f"status={status_name}, assigned={len(assignments)}"
    result_status = "SOLVED"
    if not feasible:
        result_status = "INFEASIBLE"
    elif unscheduled:
        result_status = "PARTIAL"

    return GenerationResult(
        status=result_status,
        objective_value=solver.ObjectiveValue() if feasible else None,
        solve_time=time.time() - t0,
        assignments=assignments,
        unscheduled_event_ids=unscheduled,
        message=msg,
    )
