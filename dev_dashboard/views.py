"""Представления dev-панели. Доступен только при DEBUG=True (см. apps/urls)."""

from __future__ import annotations

import json

from django import forms
from django.contrib import messages
from django.shortcuts import render
from django.views.decorators.http import require_POST

from schedule_generator.models import GenerationRun, Module, Semester
from schedule_generator.services import GenerationError, run_generation

from .checks import run_all_checks


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


def dashboard(request):
    form = GenerateForm(initial={"time_limit": 10, "seed": 17})
    # Подставляем актуальный список семестров/модулей на каждой отрисовке.
    form.fields["semester"].queryset = Semester.objects.order_by("-start_date")
    form.fields["module"].queryset = Module.objects.order_by("semester", "number")
    runs = GenerationRun.objects.select_related("semester", "module")[:10]
    context = {
        "checks": run_all_checks(),
        "form": form,
        "runs": runs,
        "last_run_json": _last_run_dump(),
    }
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
                "room": sc.room.name if sc.room_id else None,
            }
            for sc in run.scheduled_classes.select_related(
                "slot", "room", "event__subject", "event__group")
        ],
        "issues": [{"level": i.level, "text": i.text} for i in run.issues.all()],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
