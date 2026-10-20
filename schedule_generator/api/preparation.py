"""Контракты запросов импорта и подготовки нагрузки; вычисления — в services."""

from rest_framework import serializers

from ..enums import LessonType
from ..models import Module


class CurriculumImportRequestSerializer(serializers.Serializer):
    document = serializers.JSONField(help_text="curriculum.json, schema_version=1")
    review = serializers.JSONField(
        help_text="SHA-256, выбранные строки/семестры и подтверждённые исправления"
    )


class AllocationEntrySerializer(serializers.Serializer):
    curriculum_entry_id = serializers.IntegerField(min_value=1)
    lesson_type = serializers.ChoiceField(
        choices=[LessonType.LECTURE, LessonType.PRACTICE, LessonType.LAB]
    )
    academic_hours = serializers.IntegerField(min_value=1)
    teacher_id = serializers.IntegerField(min_value=1)


class ModuleLoadRequestSerializer(serializers.Serializer):
    module = serializers.PrimaryKeyRelatedField(queryset=Module.objects.all())
    semester_number = serializers.IntegerField(min_value=1)
    academic_hours_per_pair = serializers.IntegerField(min_value=1, default=2)
    replace = serializers.BooleanField(default=False)
    entries = AllocationEntrySerializer(many=True, allow_empty=False)


class CurriculumImportResultSerializer(serializers.Serializer):
    curriculum_id = serializers.IntegerField()
    curriculum_created = serializers.BooleanField()
    created = serializers.IntegerField()
    updated = serializers.IntegerField()
    entry_ids = serializers.ListField(child=serializers.IntegerField())
    unselected_source_rows = serializers.IntegerField()


class ModuleLoadResultSerializer(serializers.Serializer):
    group_id = serializers.IntegerField()
    module_id = serializers.IntegerField()
    created = serializers.IntegerField()
    updated = serializers.IntegerField()
    removed = serializers.IntegerField()
    event_ids = serializers.ListField(child=serializers.IntegerField())
    academic_hours = serializers.IntegerField()
