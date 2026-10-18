"""Представления dev-панели. Доступен только при DEBUG=True (см. apps/urls)."""

from __future__ import annotations

import json

from django import forms
from django.contrib import messages
from django.db import models
from django.shortcuts import render
from django.views.decorators.http import require_POST

from schedule_generator.models import (
    GenerationRun,
    Group,
    Module,
    ScheduledClass,
    Semester,
    Student,
)
from schedule_generator.services import GenerationError, run_generation

from .checks import run_all_checks
from .student_view import (
    DAY_NAMES,
    build_student_schedule,
    get_latest_run,
    pick_representative_students,
    room_display,
)


class GenerateForm(forms.Form):
    """Форма запуска генерации через тот же сервисный слой, что и API."""

    semester = forms.ModelChoiceField(queryset=Semester.objects.all(),
                                      label="Семестр", empty_label=None)
    module = forms.ModelChoiceField(queryset=Module.objects.all(),
                                    label="Модуль (если есть модульные занятия)",
                                    required=False)
    time_limit = forms.IntegerField(label="Лимит решения, сек", min_value=1,
                                    max_value=120, initial=10)
    seed = forms.IntegerField(label="Seed решателя", min_value=0, initial=17)
    clear_previous = forms.BooleanField(label="Удалить старые пары периода",
                                        initial=True, required=False)


def _student_schedules_context(run_id: int | None = None,
                               student_id: int | None = None,
                               group_id: int | None = None,
                               group_sort: str = "name",
                               direction: str = "asc"):
    """Расписание глазами студентов для последнего (или выбранного) запуска.

    Если передан student_id — показываем расписание конкретного студента;
    иначе — подборку представителей. Рядом всегда отдаём список групп с
    сортировкой (по названию / количеству студентов), из которого можно
    выбрать студента.
    """
    run = None
    if run_id:
        run = GenerationRun.objects.filter(pk=run_id).first()
    if run is None:
        run = get_latest_run()
    if run is None:
        return {"student_runs": [], "selected_run_id": None, "student_run": None,
                "group_rows": []}

    groups = list(Group.objects.filter(semester=run.semester)
                  .annotate(students_count=models.Count("students")))
    reverse = direction == "desc"
    if group_sort == "students":
        groups.sort(key=lambda g: (g.students_count, g.name), reverse=reverse)
    else:
        groups.sort(key=lambda g: g.name, reverse=reverse)
    group_rows = [{"group": g, "count": g.students_count} for g in groups]

    students: list[Student] = []
    selected_group_id = _int_or_none(group_id)
    if selected_group_id and not student_id:
        # Выбрана группа — показываем расписание её студентов (с лимитом).
        students = list(Student.objects.filter(group_id=selected_group_id)
                        .order_by("full_name")[:4])
    selected_student = None
    if student_id:
        selected_student = Student.objects.filter(pk=student_id).first()
        if selected_student is not None:
            students = [selected_student]
    if not students:
        students = pick_representative_students(run)
    schedules = [build_student_schedule(run, s) for s in students]
    runs = [(r.pk, f"Run #{r.pk} · {r.semester} · {r.status}")
            for r in GenerationRun.objects.order_by("-created_at")[:10]]
    # Корпуса, в которых реально проходят пары этого запуска: каждый корпус —
    # отдельная площадка (далеко от других), поэтому сводка идёт по корпусам.
    building_rows = []
    for bld_count in (ScheduledClass.objects
                      .filter(run=run, room__building__isnull=False)
                      .values("room__building__name")
                      .annotate(pairs=models.Count("id"))
                      .order_by("room__building__name")):
        building_rows.append({"name": bld_count["room__building__name"],
                              "pairs": bld_count["pairs"]})
    return {
        "day_names": DAY_NAMES,
        "student_schedules": schedules,
        "student_run": run,
        "student_runs": runs,
        "selected_run_id": run.pk,
        "selected_student_id": selected_student.pk if selected_student else None,
        "selected_group_id": selected_group_id,
        "group_rows": group_rows,
        "group_sort": group_sort,
        "group_direction": direction,
        "group_students_total": sum(g["count"] for g in group_rows),
        "run_buildings": building_rows,
    }


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _student_options_context(run_id: int | None):
    """Группы и студенты для выпадающего выбора «расписание студента»."""
    run = GenerationRun.objects.filter(pk=run_id).first() if run_id else get_latest_run()
    if run is None:
        return []
    students = (Student.objects.filter(group__semester=run.semester)
                .select_related("group").order_by("group__name", "full_name"))
    options: list[dict] = []
    current_group = None
    for student in students:
        if student.group_id != current_group:
            current_group = student.group_id
            options.append({"label": f"— {student.group.name}"
                                     + (" (ДПО)" if student.group.is_dpo else "")
                                     + " —", "value": None})
        options.append({"label": student.full_name, "value": student.pk})
    return options


def dashboard(request):
    form = GenerateForm(initial={"time_limit": 10, "seed": 17})
    # Подставляем актуальный список семестров/модулей на каждой отрисовке.
    form.fields["semester"].queryset = Semester.objects.order_by("-start_date")
    form.fields["module"].queryset = Module.objects.order_by("semester", "number")
    runs = GenerationRun.objects.select_related("semester", "module")[:10]
    selected_run_id = request.GET.get("run") or request.session.get("dev_last_run_id")
    context = {
        "checks": run_all_checks(),
        "form": form,
        "runs": runs,
        "last_run_json": _last_run_dump(),
        **_student_schedules_context(
            selected_run_id,
            student_id=_int_or_none(request.GET.get("student")),
            group_id=_int_or_none(request.GET.get("group")),
            group_sort=request.GET.get("sort") or "name",
            direction="desc" if request.GET.get("dir") == "desc" else "asc",
        ),
    }
    context["student_options"] = _student_options_context(context["selected_run_id"])
    if context["student_run"] is not None:
        request.session["dev_last_run_id"] = context["student_run"].pk
    return render(request, "dev_dashboard/dashboard.html", context)


@require_POST
def generate(request):
    form = GenerateForm(request.POST)
    # Перезаполняем queryset'ы — после биндинга их надо обновить вручную.
    form.fields["semester"].queryset = Semester.objects.all()
    form.fields["module"].queryset = Module.objects.all()
    if not form.is_valid():
        messages.error(request, f"Проверьте форму: {form.errors.as_json()}")
    else:
        data = form.cleaned_data
        try:
            outcome = run_generation(
                semester=data["semester"],
                module=data["module"],
                time_limit_seconds=data["time_limit"],
                seed=data["seed"],
                clear_previous=data["clear_previous"],
            )
            run = GenerationRun.objects.get(pk=outcome.run_id)
            request.session["dev_last_run_id"] = run.pk
            level = ("success" if run.status == "SOLVED" else "warning")
            messages.add_message(request, getattr(messages, level),
                                 f"Run #{run.pk}: статус {run.status}, "
                                 f"пар {run.scheduled_classes.count()}, "
                                 f"проблем {run.issues.count()}.")
        except GenerationError as exc:
            messages.error(request, f"Генерация не запущена: {exc}")
        except Exception as exc:  # noqa: BLE001 - dev-панель показывает любую ошибку
            messages.error(request, f"Ошибка генерации: {exc!r}")
    return render(request, "dev_dashboard/dashboard.html", {
        "checks": run_all_checks(),
        "form": form,
        "runs": GenerationRun.objects.select_related("semester", "module")[:10],
        "last_run_json": _last_run_dump(),
        **_student_schedules_context(request.session.get("dev_last_run_id")),
        "student_options": [],
    })


def _last_run_dump() -> str:
    """Последний запуск в JSON для блока «сырые данные» (проверка сериализации)."""
    run = (GenerationRun.objects.prefetch_related("scheduled_classes__slot",
                                                  "scheduled_classes__room",
                                                  "issues").order_by("-created_at").first())
    if run is None:
        return "{}"
    payload = {
        "run_id": run.pk,
        "status": run.status,
        "objective_value": run.objective_value,
        "solve_time_seconds": run.solve_time_seconds,
        "classes": [
            {
                "day": sc.slot.get_day_of_week_display(),
                "lesson": sc.slot.lesson_number,
                "subject": sc.event.subject.name,
                "type": sc.event.lesson_type,
                "group": sc.event.group.name if sc.event.group_id else None,
                "dpo_group": sc.event.group.is_dpo if sc.event.group_id else None,
                "room": room_display(sc, online_label="онлайн"),
            }
            for sc in run.scheduled_classes.select_related(
                "slot", "room__building", "event__subject", "event__group")
        ],
        "issues": [{"level": i.level, "text": i.text} for i in run.issues.all()],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
