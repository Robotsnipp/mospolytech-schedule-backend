"""Автопроверки корректности состояния проекта (для dev-панели).

Каждая проверка возвращает CheckResult(name, status, message), где status:
"ok" | "warning" | "error". Проверки читают реальные данные БД и
последний GenerationRun, поэтому панель показывает фактическое
состояние проекта, а не заглушки.

Инварианты взяты из CONTEXT.md / enums.py / services/:
- PE не участвует в генерации;
- лекции онлайн на поток, ПД очно на студентов, ДПО — на группу ДПО;
- для каждого ДПО — отдельная группа (Group.is_dpo=True);
- в одном слоте аудитория/группа/преподаватель не могут быть заняты дважды.

Обратите внимание (AGENTS.md): статус SOLVED сам по себе не доказывает
корректность правил, поэтому проверки полноты нагрузки и конфликтов
считаются отдельно от статуса запуска.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db.models import Count

from schedule_generator.enums import Format, LessonType
from schedule_generator.models import (
    Building,
    Campus,
    GenerationRun,
    Group,
    LessonEvent,
    Room,
    ScheduledClass,
    Semester,
    Student,
    TimeSlot,
)

OK = "ok"
WARNING = "warning"
ERROR = "error"


@dataclass
class CheckResult:
    name: str
    status: str  # OK | WARNING | ERROR
    message: str


def _latest_run() -> GenerationRun | None:
    return GenerationRun.objects.order_by("-created_at").first()


def _no_run() -> CheckResult:
    return CheckResult("Генерация", WARNING,
                       "Запусков генерации ещё не было — нажмите «Запустить генерацию».")


# ------------------------------------------------------------------
# Данные перед генерацией
# ------------------------------------------------------------------

def check_semesters() -> CheckResult:
    semesters = Semester.objects.order_by("-start_date")
    if not semesters.exists():
        return CheckResult("Семестры", ERROR, "В БД нет ни одного семестра.")
    latest = semesters.first()
    slots = TimeSlot.objects.filter(week_pattern__semester=latest).count()
    groups = Group.objects.filter(semester=latest).count()
    events = LessonEvent.objects.filter(semester=latest).count()
    if slots == 0:
        return CheckResult("Семестры", ERROR,
                           f"«{latest}»: нет слотов шаблона недели — генерация невозможна.")
    if events == 0:
        return CheckResult("Семестры", WARNING,
                           f"«{latest}»: слотов {slots}, но занятий (LessonEvent) нет.")
    return CheckResult("Семестры", OK,
                       f"Последний: «{latest}», слотов {slots}, групп {groups}, "
                       f"занятий {events}.")


def check_resources() -> CheckResult:
    active = Room.objects.filter(is_active=True)
    if not active.exists():
        return CheckResult("Ресурсы", ERROR, "Нет ни одной активной аудитории.")
    onsite_needs = (LessonEvent.objects
                    .filter(format=Format.ONSITE)
                    .values_list("audience_size", flat=True))
    tight = [n for n in onsite_needs
             if n and not active.filter(capacity__gte=n).exists()]
    msg = f"Активных аудиторий: {active.count()}."
    if tight:
        return CheckResult("Ресурсы", WARNING,
                           msg + f" Для {len(tight)} очных событий нет аудитории по "
                                 "вместимости — такие пары решатель пропустит (см. PARTIAL).")
    return CheckResult("Ресурсы", OK, msg)


def check_buildings() -> CheckResult:
    """Корпуса и площадки: каждый корпус — отдельная площадка.

    Корпуса (Михалковская, Прянишникова, ...) далеко друг от друга (~час
    езды), поэтому объединять их в одну площадку нельзя: проверяем, что у
    каждого корпуса ровно своя площадка и нет корпусов без площадки.
    """
    buildings = Building.objects.select_related("campus")
    if not buildings.exists():
        return CheckResult("Корпуса и площадки", WARNING,
                           "В БД нет ни одного корпуса — запустите демо-данные "
                           "или заведите корпуса с площадками.")
    no_campus = buildings.filter(campus__isnull=True).count()
    shared = (Campus.objects.annotate(n=Count("buildings")).filter(n__gt=1)
              .values_list("name", flat=True))
    problems = []
    if no_campus:
        problems.append(f"{no_campus} корпусов без площадки")
    if shared:
        problems.append("площадки объединяют несколько корпусов: "
                        + ", ".join(shared))
    if problems:
        return CheckResult("Корпуса и площадки", ERROR, "; ".join(problems)
                           + " — корпуса далеко друг от друга и не должны "
                           "объединяться в общие площадки.")
    rooms_no_building = Room.objects.filter(building__isnull=True).count()
    msg = (f"Корпусов: {buildings.count()}, площадок: {Campus.objects.count()} "
           f"(по одной на корпус).")
    if rooms_no_building:
        return CheckResult("Корпуса и площадки", WARNING,
                           msg + f" Аудиторий без корпуса: {rooms_no_building} — "
                                 "для них правило площадок не применяется.")
    return CheckResult("Корпуса и площадки", OK, msg)


def check_dpo_groups() -> CheckResult:
    """Для каждого ДПО — отдельная группа; занятия ДПО висят только на таких группах."""
    dpo_events = LessonEvent.objects.filter(lesson_type=LessonType.DPO)
    dpo_groups = Group.objects.filter(is_dpo=True)
    if not dpo_events.exists():
        return CheckResult("Группы ДПО", WARNING,
                           "Занятий ДПО в базе нет — заведите группу с is_dpo=True "
                           "и LessonEvent типа DPO (или запустите демо-данные).")
    problems = []
    bad_events = dpo_events.exclude(group__is_dpo=True).count()
    if bad_events:
        problems.append(f"{bad_events} событий ДПО привязаны не к группе ДПО")
    # Совпадение с LessonEvent.clean(): на группу ДПО вешать можно только
    # ДПО и внеучебку (PE генератор всё равно пропускает).
    non_dpo_on_dpo = (LessonEvent.objects
                      .exclude(lesson_type__in=[LessonType.DPO,
                                                LessonType.EXTRACURRICULAR,
                                                LessonType.PE])
                      .filter(group__is_dpo=True).count())
    if non_dpo_on_dpo:
        problems.append(f"{non_dpo_on_dpo} обычных занятий висят на группах ДПО")
    orphan_groups = dpo_groups.exclude(events__in=dpo_events).count()
    if orphan_groups:
        problems.append(f"{orphan_groups} групп ДПО без занятий ДПО")
    if problems:
        return CheckResult("Группы ДПО", ERROR, "; ".join(problems) + ".")
    students = Student.objects.filter(group__is_dpo=True).count()
    online = dpo_events.filter(format=Format.ONLINE).count()
    onsite = dpo_events.filter(format=Format.ONSITE).count()
    return CheckResult("Группы ДПО", OK,
                       f"Событий ДПО: {dpo_events.count()} (онлайн {online}, очных {onsite}), "
                       f"групп ДПО: {dpo_groups.count()}, слушателей: {students}. "
                       "Каждое ДПО — своя группа.")


def check_event_integrity() -> CheckResult:
    """Сверка данных с правилами clean(); кривые события генератор пропустит."""
    problems = []
    lectures_no_stream = (LessonEvent.objects.filter(lesson_type=LessonType.LECTURE)
                          .filter(stream__isnull=True).count())
    if lectures_no_stream:
        problems.append(f"лекций без потока: {lectures_no_stream}")
    pd_no_project = (LessonEvent.objects.filter(lesson_type=LessonType.PD)
                     .filter(project__isnull=True).count())
    if pd_no_project:
        problems.append(f"ПД без проекта: {pd_no_project}")
    zero_pairs = LessonEvent.objects.filter(pairs_per_week=0).count()
    if zero_pairs:
        problems.append(f"занятий с 0 пар: {zero_pairs}")
    if problems:
        return CheckResult("Целостность занятий", WARNING,
                           "; ".join(problems) + " — такие события попадут в issues.")
    total = LessonEvent.objects.count()
    pe = LessonEvent.objects.filter(lesson_type=LessonType.PE).count()
    return CheckResult("Целостность занятий", OK,
                       f"Всего занятий {total}; физкультуры {pe} (исключена из генерации).")


# ------------------------------------------------------------------
# Результат последнего запуска
# ------------------------------------------------------------------

def check_last_run() -> CheckResult:
    run = _latest_run()
    if run is None:
        return _no_run()
    stats = (f"Run #{run.pk}: статус {run.status}, пар {run.scheduled_classes.count()}, "
             f"проблем {run.issues.count()}, время {run.solve_time_seconds or 0:.2f}с")
    if run.status == "PENDING":
        return CheckResult("Последний запуск", WARNING, stats + " — завис в PENDING.")
    if run.status == "SOLVED":
        return CheckResult("Последний запуск", OK, stats)
    if run.status == "PARTIAL":
        return CheckResult("Последний запуск", WARNING, stats + " — не всё размещено.")
    return CheckResult("Последний запуск", ERROR, f"{stats}. {run.message}")


def check_load_coverage() -> CheckResult:
    """Полнота нагрузки: все требуемые генерируемые пары попали в расписание."""
    run = _latest_run()
    if run is None:
        return _no_run()
    events = (LessonEvent.objects.filter(semester=run.semester)
              .exclude(lesson_type=LessonType.PE))
    planned = sum(e.pairs_per_week for e in events)
    placed = run.scheduled_classes.count()
    skipped = planned - placed
    if skipped > 0:
        hints = list(run.issues.values_list("text", flat=True)[:3])
        return CheckResult("Полнота нагрузки", WARNING,
                           f"Требовалось {planned} пар, размещено {placed}, пропущено {skipped}. "
                           + "; ".join(hints))
    if placed == 0:
        return CheckResult("Полнота нагрузки", WARNING,
                           "Размещать было нечего (нет генерируемых занятий семестра).")
    return CheckResult("Полнота нагрузки", OK,
                       f"Все {placed} требуемых пар размещены.")


def check_room_conflicts() -> CheckResult:
    run = _latest_run()
    if run is None:
        return _no_run()
    dupes = (ScheduledClass.objects.filter(run=run, room__isnull=False)
             .values("slot", "room").annotate(n=Count("id")).filter(n__gt=1).count())
    if dupes:
        return CheckResult("Конфликты аудиторий", ERROR,
                           f"{dupes} коллизий: аудитория занята двумя парами в одном слоте "
                           "(инвариант решателя нарушен!).")
    online_with_room = (ScheduledClass.objects.filter(run=run, event__format=Format.ONLINE)
                        .exclude(room__isnull=True).count())
    if online_with_room:
        return CheckResult("Конфликты аудиторий", WARNING,
                           f"{online_with_room} онлайн-пар получили аудиторию.")
    return CheckResult("Конфликты аудиторий", OK,
                       "Ни одна аудитория не занята дважды в один слот.")


def check_group_conflicts() -> CheckResult:
    run = _latest_run()
    if run is None:
        return _no_run()
    dupes = (ScheduledClass.objects.filter(run=run, event__group__isnull=False)
             .values("slot", "event__group").annotate(n=Count("id")).filter(n__gt=1).count())
    if dupes:
        return CheckResult("Конфликты групп", ERROR,
                           f"{dupes} коллизий: группа (в т.ч. ДПО) занята двумя парами в слоте.")
    return CheckResult("Конфликты групп", OK,
                       "Учебные группы и группы ДПО не пересекаются в слотах.")


def check_teacher_conflicts() -> CheckResult:
    run = _latest_run()
    if run is None:
        return _no_run()
    dupes = (ScheduledClass.objects.filter(run=run, event__teacher__isnull=False)
             .values("slot", "event__teacher").annotate(n=Count("id")).filter(n__gt=1).count())
    if dupes:
        return CheckResult("Конфликты преподавателей", ERROR,
                           f"{dupes} коллизий: преподаватель ведёт две пары в одном слоте.")
    return CheckResult("Конфликты преподавателей", OK,
                       "Преподаватель не ведёт две пары одновременно.")


def check_campus_trips() -> CheckResult:
    """Перемещения между площадками: пары последнего запуска размещены по
    корпусам; участник не бывает в двух корпусах-площадках в одном слоте."""
    run = _latest_run()
    if run is None:
        return _no_run()
    onsite = ScheduledClass.objects.filter(run=run, room__building__isnull=False)
    total = onsite.count()
    if total == 0:
        return CheckResult("Перемещения между площадками", WARNING,
                           "В запуске нет очных пар с аудиторией из корпуса.")
    dupes = (onsite.values("slot", "event__group")
             .annotate(n=Count("id")).filter(n__gt=1).count())
    if dupes:
        return CheckResult("Перемещения между площадками", ERROR,
                           f"{dupes} коллизий: группа занята двумя очными парами "
                           "в одном слоте (разные корпуса — переход невозможен).")
    buildings = onsite.values_list("room__building__name", flat=True).distinct().count()
    return CheckResult("Перемещения между площадками", OK,
                       f"Очных пар: {total}, корпусов задействовано: {buildings}. "
                       "Каждый корпус — отдельная площадка (~час езды между ними), "
                       "объединение корпусов в площадки запрещено проверкой «Корпуса и площадки».")


ALL_CHECKS = (
    check_semesters,
    check_resources,
    check_buildings,
    check_dpo_groups,
    check_event_integrity,
    check_last_run,
    check_load_coverage,
    check_room_conflicts,
    check_group_conflicts,
    check_teacher_conflicts,
    check_campus_trips,
)


def run_all_checks() -> list[CheckResult]:
    results = []
    for check in ALL_CHECKS:
        try:
            results.append(check())
        except Exception as exc:  # noqa: BLE001 - панель не должна падать
            results.append(CheckResult(check.__name__, ERROR, f"Ошибка проверки: {exc!r}"))
    return results
