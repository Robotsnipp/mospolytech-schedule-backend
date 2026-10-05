"""Сериализаторы API генератора расписания (слой представления).

Ответственность: только валидация входных данных HTTP и форматирование
выходных. Бизнес-логика — в services/.
"""

from rest_framework import serializers

from ..models import GenerationRun, ScheduledClass, Semester, WeekPattern


class GenerateRequestSerializer(serializers.Serializer):
    """Параметры запуска генерации недельного шаблона расписания."""

    semester = serializers.PrimaryKeyRelatedField(queryset=Semester.objects.all())
    week_pattern = serializers.PrimaryKeyRelatedField(
        queryset=WeekPattern.objects.all(), required=False, allow_null=True,
        help_text="Шаблон недели; по умолчанию — первый паттерн семестра.",
    )
    time_limit_seconds = serializers.IntegerField(required=False, default=30, min_value=1,
                                                  max_value=600)
    seed = serializers.IntegerField(required=False, default=17)
    clear_previous = serializers.BooleanField(
        required=False, default=True,
        help_text="Удалить пары предыдущих запусков этого семестра перед записью новых.",
    )

    def validate(self, attrs):
        wp = attrs.get("week_pattern")
        semester = attrs["semester"]
        if wp is not None and wp.semester_id != semester.id:
            raise serializers.ValidationError(
                {"week_pattern": "Шаблон недели относится к другому семестру."}
            )
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
        fields = ["id", "semester", "created_at", "status", "objective_value",
                  "solve_time_seconds", "message", "scheduled_classes", "issues"]
