"""Discover prepared generator data, independently of the published rasp directory."""

from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from ..management.commands.seed_generation_example import NO_PD_PROFILE
from ..models import FixedProjectSession, Group, LessonEvent, Semester, Teacher
from ..services.resource_profiles import available_resource_profiles
from ..services.settings import NUMERIC_RULES, default_settings


class GenerationOptionsSerializer(serializers.Serializer):
    semesters = serializers.ListField(child=serializers.DictField())
    authentication_required = serializers.BooleanField()
    default_semester_id = serializers.IntegerField(allow_null=True)
    settings_schema_version = serializers.IntegerField()
    resource_profiles = serializers.ListField(child=serializers.DictField())
    settings_defaults = serializers.DictField()
    rules_catalog = serializers.DictField()


@extend_schema(responses=GenerationOptionsSerializer)
@api_view(["GET"])
@permission_classes([AllowAny])
def generation_options(request):
    result = []
    recommended = []
    profiles = available_resource_profiles()
    profile_semesters = {item["semester_id"] for item in profiles}
    for semester in Semester.objects.filter(events__isnull=False).distinct().order_by("id"):
        events = LessonEvent.objects.filter(semester=semester).select_related(
            "group", "curriculum_entry__curriculum", "project").prefetch_related("stream__groups")
        group_ids = set()
        notices = set()
        example = False
        teacher_ids = set()
        for event in events:
            if event.teacher_id:
                teacher_ids.add(event.teacher_id)
            if event.project_id and event.project.supervisor_id:
                teacher_ids.add(event.project.supervisor_id)
            if event.group_id:
                group_ids.add(event.group_id)
            if event.prepared_for_group_id:
                group_ids.add(event.prepared_for_group_id)
            if event.stream_id:
                group_ids.update(group.pk for group in event.stream.groups.all())
            if event.curriculum_entry_id:
                source = event.curriculum_entry.curriculum.source
                example = example or bool(source.get("example_seed"))
                if source.get("example_seed") == NO_PD_PROFILE and semester.pk not in profile_semesters:
                    recommended.append(semester.pk)
                notice = source.get("example_notice")
                if notice:
                    notices.add(notice)
        for teacher_id, supervisor_id in FixedProjectSession.objects.filter(project__semester=semester).values_list(
            "teacher_id", "project__supervisor_id"
        ):
            if teacher_id or supervisor_id:
                teacher_ids.add(teacher_id or supervisor_id)
        groups = [{"id": group.pk, "name": group.name,
                   "student_count": group.students.count()}
                  for group in Group.objects.filter(pk__in=group_ids).order_by("id")]
        result.append({
            "id": semester.pk, "name": semester.name, "groups": groups,
            "is_archived": bool(profiles and example and semester.pk not in profile_semesters),
            "display_name": (
                f"{'Осень' if semester.start_date.month >= 7 else 'Весна'} {semester.start_date.year} · демо с реальными аудиториями"
                if semester.pk in profile_semesters else semester.name),
            "modules": list(semester.modules.order_by("number").values(
                "id", "number", "start_date", "end_date", "teaching_weeks")),
            "patterns": [{"id": pattern.pk, "name": pattern.name,
                          "bell_schedule": pattern.bell_schedule,
                          "slots": list(pattern.slots.order_by("day_of_week", "lesson_number").values(
                              "id", "day_of_week", "lesson_number", "start_time", "end_time"))}
                         for pattern in semester.week_patterns.order_by("id")],
            "teachers": [{"id": teacher.pk, "name": teacher.full_name}
                         for teacher in Teacher.objects.filter(pk__in=teacher_ids).order_by("id")],
            "notice": (
                "Реальный учебный план набора 2025; состав, преподаватели и распределение по модулям тестовые. "
                "Обозначения помещений реальные, вместимость — отдельное допущение демо. ПД и физкультура не включены."
                if semester.pk in profile_semesters else " ".join(sorted(notices))) or (
                "Подготовленная нагрузка генератора; опубликованное расписание rasp не используется. "
                "Выберите одну группу; это контролируемый срез, не занятость всего вуза."),
        })
    return Response({"semesters": result, "authentication_required": False,
                     "settings_schema_version": 1, "resource_profiles": profiles,
                     "settings_defaults": default_settings(),
                     "rules_catalog": {
                         **{key: {"min": low, "max": high, "modes": ["preferred", "required"], "default_enabled": False}
                            for key, (low, high) in NUMERIC_RULES.items()},
                         "free_days": {"days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
                                       "modes": ["preferred", "required"]},
                         "earliest_start": {"format": "HH:MM", "default_enabled": False},
                         "latest_end": {"format": "HH:MM", "default_enabled": False},
                     },
                     "default_semester_id": (max(profile_semesters) if profile_semesters else
                                             max(recommended) if recommended else (result[0]["id"] if result else None))})
