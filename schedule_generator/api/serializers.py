"""Сериализаторы API генератора расписания (слой представления).

Ответственность: только валидация входных данных HTTP и форматирование
выходных. Бизнес-логика — в services/. CRUD-сериализаторы справочников —
в api/crud.py (реэкспортируются здесь, чтобы весь API-контракт был виден
из одного модуля).
"""

from __future__ import annotations

from rest_framework import serializers

from ..models import Module, Semester, WeekPattern
from .crud import (  # noqa: F401  (реэкспорт для импортов из этого модуля)
    BuildingSerializer,
    CurriculumEntrySerializer,
    CurriculumSerializer,
    GenerationIssueSerializer,
    GenerationRunListSerializer,
    GenerationRunSerializer,
    GroupSerializer,
    LectureStreamSerializer,
    LessonEventSerializer,
    LocationSerializer,
    ModuleSerializer,
    ProjectEnrollmentSerializer,
    ProjectSerializer,
    RoomSerializer,
    ScheduledClassSerializer,
    SemesterSerializer,
    StudentSerializer,
    SubjectSerializer,
    TeacherSerializer,
    TimeSlotSerializer,
    WeekPatternSerializer,
)


class GenerateRequestSerializer(serializers.Serializer):
    """Параметры запуска генерации недельного шаблона расписания."""

    semester = serializers.PrimaryKeyRelatedField(queryset=Semester.objects.all())
    module = serializers.PrimaryKeyRelatedField(
        queryset=Module.objects.all(), required=False, allow_null=True,
        help_text="Календарный модуль; если у семестра есть модульные занятия — обязателен.",
    )
    week_pattern = serializers.PrimaryKeyRelatedField(
        queryset=WeekPattern.objects.all(), required=False, allow_null=True,
        help_text="Шаблон недели; по умолчанию — первый паттерн семестра.",
    )
    time_limit_seconds = serializers.IntegerField(required=False, default=30, min_value=1,
                                                  max_value=600)
    seed = serializers.IntegerField(required=False, default=17)
    clear_previous = serializers.BooleanField(
        required=False, default=True,
        help_text="Заменить пары этого периода только при полном успешном результате.",
    )

    def validate(self, attrs):
        week_pattern = attrs.get("week_pattern")
        semester = attrs["semester"]
        if week_pattern is not None and week_pattern.semester_id != semester.id:
            raise serializers.ValidationError(
                {"week_pattern": "Шаблон недели относится к другому семестру."}
            )
        module = attrs.get("module")
        if module is not None and module.semester_id != semester.id:
            raise serializers.ValidationError({"module": "Модуль относится к другому семестру."})
        return attrs


# ---------------------------------------------------------------------------
# Параметры фильтрации read-only действий (для корректной схемы в Swagger UI)
# ---------------------------------------------------------------------------

class ScheduleFilterSerializer(serializers.Serializer):
    """Query-параметры просмотра пар расписания (group / stream / teacher)."""

    semester = serializers.PrimaryKeyRelatedField(queryset=Semester.objects.all())
    module = serializers.PrimaryKeyRelatedField(
        queryset=Module.objects.all(), required=False, allow_null=True)
    run = serializers.IntegerField(
        required=False, help_text="ID конкретного запуска; по умолчанию — последний завершённый.")


class PatternScheduleFilterSerializer(ScheduleFilterSerializer):
    """Дополнительно фильтр по шаблону недели (чётной/нечётной)."""

    week_pattern = serializers.PrimaryKeyRelatedField(
        queryset=WeekPattern.objects.all(), required=False, allow_null=True)
