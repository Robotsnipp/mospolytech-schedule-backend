"""Типизированный ответ просмотра расписания для фронтенда."""

from rest_framework import serializers

from ..models import GenerationRun
from .crud import GenerationIssueSerializer, ScheduledClassSerializer


class ScheduleRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = GenerationRun
        fields = ["id", "semester", "module", "created_at", "status", "message",
                  "objective_value", "solve_time_seconds"]


class FixedPDSessionSerializer(serializers.Serializer):
    run = serializers.IntegerField()
    session_id = serializers.IntegerField()
    project_id = serializers.IntegerField()
    project_title = serializers.CharField()
    lesson_type = serializers.CharField()
    format = serializers.CharField()
    day_of_week = serializers.IntegerField()
    start = serializers.TimeField()
    end = serializers.TimeField()
    date = serializers.DateField(allow_null=True)
    occurrence_dates = serializers.ListField(child=serializers.DateField())
    teacher_id = serializers.IntegerField(allow_null=True)
    teacher_name = serializers.CharField(allow_null=True)
    room_id = serializers.IntegerField(allow_null=True)
    room_name = serializers.CharField(allow_null=True)
    location_name = serializers.CharField(allow_blank=True)
    student_ids = serializers.ListField(child=serializers.IntegerField())
    participant_group_ids = serializers.ListField(child=serializers.IntegerField())
    travel_buffer_minutes = serializers.IntegerField()
    place_details = serializers.JSONField()
    source = serializers.JSONField()


class ScheduleResponseSerializer(serializers.Serializer):
    run = ScheduleRunSerializer(allow_null=True)
    scheduled_classes = ScheduledClassSerializer(many=True)
    fixed_pd_snapshot = FixedPDSessionSerializer(many=True)
    issues = GenerationIssueSerializer(many=True)
