"""Сериализаторы ответов API для фронтенда (только чтение, схема для Swagger).

Эти классы описывают форму агрегированных ответов из api/frontend.py:
сетка недели, календарная неделя с датами, карточка «моё расписание».
Данные собираются viewset'ами вручную (плоские dict'и), сериализаторы
служят схемой ответа для drf-spectacular и финальной нормализацией.
"""

from __future__ import annotations

from rest_framework import serializers


class LabeledChoiceSerializer(serializers.Serializer):
    """Пункт словаря: machine value + человекочитаемая подпись."""

    value = serializers.CharField()
    label = serializers.CharField()


class SemesterBriefSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    start_date = serializers.DateField()
    weeks_count = serializers.IntegerField()


class BootstrapSerializer(serializers.Serializer):
    """Ответ /frontend/bootstrap/."""

    lesson_types = LabeledChoiceSerializer(many=True)
    formats = LabeledChoiceSerializer(many=True)
    run_statuses = LabeledChoiceSerializer(many=True)
    week_parity = LabeledChoiceSerializer(many=True)
    semesters = SemesterBriefSerializer(many=True)


class WeekModuleSerializer(serializers.Serializer):
    module_id = serializers.IntegerField()
    number = serializers.IntegerField()
    start_date = serializers.DateField()
    end_date = serializers.DateField()
    teaching_weeks = serializers.IntegerField()
    first_monday = serializers.DateField()


class WeekListSerializer(serializers.Serializer):
    """Ответ /frontend/weeks/."""

    semester = SemesterBriefSerializer()
    weeks = WeekModuleSerializer(many=True)


class GridGroupSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    is_dpo = serializers.BooleanField()


class GridTeacherSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    full_name = serializers.CharField()


class GridStreamSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    label = serializers.CharField()
    groups = serializers.ListField(child=serializers.CharField())


class GridProjectSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField()
    supervisor = serializers.CharField()


class GridHolderListSerializer(serializers.Serializer):
    """Ответ /frontend/grid/holders/."""

    groups = GridGroupSerializer(many=True)
    teachers = GridTeacherSerializer(many=True)
    streams = GridStreamSerializer(many=True)
    projects = GridProjectSerializer(many=True)


class ClassCellSerializer(serializers.Serializer):
    """Пара в ячейке сетки — всё, что нужно фронту для рендера карточки."""

    id = serializers.IntegerField()
    subject = serializers.CharField()
    lesson_type = serializers.CharField()
    lesson_type_display = serializers.CharField()
    format = serializers.CharField()
    format_display = serializers.CharField()
    holder = serializers.CharField(required=False, allow_blank=True)
    teacher = serializers.CharField(allow_blank=True)
    room = serializers.CharField(allow_blank=True)
    location = serializers.CharField(allow_blank=True)
    date = serializers.DateField(required=False, allow_null=True)


class SlotSerializer(serializers.Serializer):
    slot_id = serializers.IntegerField()
    lesson_number = serializers.IntegerField()
    start_time = serializers.TimeField(allow_null=True)
    end_time = serializers.TimeField(allow_null=True)
    classes = ClassCellSerializer(many=True)


class DaySerializer(serializers.Serializer):
    day_of_week = serializers.IntegerField()
    name = serializers.CharField()
    date = serializers.DateField(required=False, allow_null=True)
    slots = SlotSerializer(many=True)


class WeekPatternRefSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    parity = serializers.IntegerField()


class WeekMatrixSerializer(serializers.Serializer):
    """Ответ /frontend/grid/ — недельная матрица носителя."""

    run_id = serializers.IntegerField(allow_null=True)
    week_pattern = WeekPatternRefSerializer(allow_null=True)
    days = DaySerializer(many=True)
    detail = serializers.CharField(required=False)


class CalendarGridSerializer(serializers.Serializer):
    """Ответ /frontend/grid/calendar/ — матрица с датами конкретной недели."""

    module = serializers.DictField(child=serializers.IntegerField())
    week = serializers.DictField()
    total_weeks = serializers.IntegerField()
    run_id = serializers.IntegerField(allow_null=True)
    days = DaySerializer(many=True)


class DayClassSerializer(ClassCellSerializer):
    lesson_number = serializers.IntegerField()
    start_time = serializers.TimeField(allow_null=True)
    end_time = serializers.TimeField(allow_null=True)


class DayScheduleSerializer(serializers.Serializer):
    """Ответ /frontend/day/ — пары одного дня."""

    date = serializers.DateField()
    day_name = serializers.CharField()
    week_pattern = serializers.IntegerField()
    run_id = serializers.IntegerField(allow_null=True)
    classes = DayClassSerializer(many=True)


class PeNoteSerializer(serializers.Serializer):
    subject = serializers.CharField()
    pairs_per_week = serializers.IntegerField()


class MeResponseSerializer(serializers.Serializer):
    """Ответ /frontend/me/students|teachers/<id>/ — карточка носителя."""

    holder_type = serializers.CharField()
    profile = serializers.DictField(child=serializers.CharField())
    run_id = serializers.IntegerField(allow_null=True)
    week_pattern = WeekPatternRefSerializer(allow_null=True)
    days = DaySerializer(many=True)
    weekly_load = serializers.IntegerField(required=False)
    limits = serializers.DictField(required=False)
    pe_notes = PeNoteSerializer(many=True, required=False)
