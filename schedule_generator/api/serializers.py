"""Сериализаторы API генератора расписания (слой представления).

Ответственность: только валидация входных данных HTTP и форматирование
выходных. Бизнес-логика — в services/. CRUD-сериализаторы справочников —
в api/crud.py (реэкспортируются здесь, чтобы весь API-контракт был виден
из одного модуля).
"""

from __future__ import annotations

from rest_framework import serializers

from ..models import (
    GenerationRun,
    Module,
    Room,
    Semester,
    Student,
    Teacher,
    WeekPattern,
)
from ..services.rooms import UNIVERSITY_TIMEZONE
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

    free_days_per_group = serializers.IntegerField(
        required=False, default=0, min_value=0, max_value=3,
        help_text="Режим подготовки к сессии: сколько дней в неделю желательно "
                  "оставить каждой группе свободными. 0 — режим выключен.",
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


# ---------------------------------------------------------------------------
# Перепланирование, совместные окна, свободные аудитории
# ---------------------------------------------------------------------------

class ReplanRequestSerializer(serializers.Serializer):
    """Сбой, после которого расписание запуска нужно перестроить минимально."""

    run = serializers.PrimaryKeyRelatedField(
        queryset=GenerationRun.objects.all(),
        help_text="Завершённый запуск (SOLVED или PARTIAL), который перестраивается.")
    teacher = serializers.PrimaryKeyRelatedField(
        queryset=Teacher.objects.all(), required=False, allow_null=True,
        help_text="Преподаватель, недоступный в дни unavailable_days.")
    unavailable_days = serializers.ListField(
        child=serializers.IntegerField(min_value=0, max_value=5), required=False,
        help_text="Дни недели недоступности преподавателя: 0 — пн … 5 — сб.")
    room = serializers.PrimaryKeyRelatedField(
        queryset=Room.objects.all(), required=False, allow_null=True,
        help_text="Закрытая аудитория (вместо преподавателя).")
    time_limit_seconds = serializers.IntegerField(required=False, default=30, min_value=1,
                                                  max_value=600)
    seed = serializers.IntegerField(required=False, default=17)


class ReplanResultSerializer(serializers.Serializer):
    """Итог перепланирования: новый запуск и перечень изменений."""

    run = serializers.IntegerField(help_text="ID нового запуска.")
    status = serializers.CharField()
    affected_count = serializers.IntegerField(help_text="Пар, затронутых сбоем.")
    unchanged_count = serializers.IntegerField(help_text="Пар, оставшихся на месте.")
    affected_students = serializers.IntegerField(help_text="Студентов с изменённым расписанием.")
    changes = serializers.ListField(
        child=serializers.DictField(),
        help_text="По событиям: что убрано (removed) и что добавлено (added).")


MAX_COMMON_FREE_STUDENTS = 20


class CommonFreeSlotsFilterSerializer(PatternScheduleFilterSerializer):
    """Query-параметры поиска общих окон студентов."""

    students = serializers.CharField(
        help_text="ID студентов через запятую, например 1,5,9 (не больше 20).")

    def validate_students(self, value):
        try:
            ids = {int(part) for part in value.split(",") if part.strip()}
        except ValueError as exc:
            raise serializers.ValidationError("Нужны числовые ID через запятую.") from exc
        if not ids:
            raise serializers.ValidationError("Укажите хотя бы одного студента.")
        if len(ids) > MAX_COMMON_FREE_STUDENTS:
            raise serializers.ValidationError(
                f"Не больше {MAX_COMMON_FREE_STUDENTS} студентов за запрос.")
        students = list(Student.objects.filter(id__in=ids).select_related("group"))
        missing = ids - {student.id for student in students}
        if missing:
            raise serializers.ValidationError(
                f"Студенты не найдены: {', '.join(map(str, sorted(missing)))}.")
        return students


class FreeRoomsFilterSerializer(PatternScheduleFilterSerializer):
    """Query-параметры поиска свободных аудиторий.

    Момент задаётся парой day_of_week + lesson_number либо временем ``at``;
    если не задано ничего, берётся текущее время университета.
    """

    day_of_week = serializers.IntegerField(required=False, min_value=0, max_value=5)
    lesson_number = serializers.IntegerField(required=False, min_value=1)
    at = serializers.DateTimeField(
        required=False, default_timezone=UNIVERSITY_TIMEZONE,
        help_text="Момент времени; без часового пояса считается московским.")
    min_capacity = serializers.IntegerField(required=False, min_value=1)
    location = serializers.IntegerField(required=False, help_text="ID локации.")

    def validate(self, attrs):
        has_day, has_lesson = "day_of_week" in attrs, "lesson_number" in attrs
        if has_day != has_lesson:
            raise serializers.ValidationError(
                "day_of_week и lesson_number задаются вместе.")
        if has_day and "at" in attrs:
            raise serializers.ValidationError(
                "Задайте либо day_of_week + lesson_number, либо at.")
        return attrs
