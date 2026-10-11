"""Сериализаторы API генератора расписания (слой представления).

Ответственность: только валидация входных данных HTTP и форматирование
выходных. Бизнес-логика — в services/.
"""

from rest_framework import serializers

from ..models import GenerationRun, ScheduledClass, Semester, WeekPattern, Module


class GenerateRequestSerializer(serializers.Serializer):
    """Параметры запуска генерации недельного шаблона расписания."""

    semester = serializers.PrimaryKeyRelatedField(queryset=Semester.objects.all())
    module = serializers.PrimaryKeyRelatedField(queryset=Module.objects.all(), required=False, allow_null=True)
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


class ScheduledClassSerializer(serializers.ModelSerializer):
    class Meta:
        model = ScheduledClass
        fields = ["id", "event", "slot", "room"]


class GenerationRunSerializer(serializers.ModelSerializer):
    scheduled_classes = ScheduledClassSerializer(many=True, read_only=True)
    issues = serializers.StringRelatedField(many=True, read_only=True)

    class Meta:
        model = GenerationRun
        fields = ["id", "semester", "module", "created_at", "status", "objective_value",
                  "solve_time_seconds", "message", "scheduled_classes", "issues"]
