"""Public read-only views of teacher-linked published schedule rules."""

from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, viewsets
from rest_framework.decorators import action

from schedule_generator.models import ImportedLesson, ScheduleImport, Teacher
from schedule_generator.services.dmami_teachers import teacher_name


class ImportedTeacherSerializer(serializers.ModelSerializer):
    source_id = serializers.SerializerMethodField()

    class Meta:
        model = Teacher
        fields = ["id", "full_name", "source_id"]

    def get_source_id(self, obj) -> str | None:
        identity = obj.dmami_identity or ""
        return identity.removeprefix("dmami:id:") if identity.startswith("dmami:id:") else None


class ImportedLessonSerializer(serializers.ModelSerializer):
    group_name = serializers.CharField(source="group.name", read_only=True)
    teachers = ImportedTeacherSerializer(many=True, read_only=True)

    class Meta:
        model = ImportedLesson
        fields = ["id", "group_name", "subject", "lesson_type", "day_of_week", "lesson_number",
                  "start", "end", "date_from", "date_to", "week_raw", "teachers",
                  "teacher_raw", "teacher_warnings", "data"]


class ImportFilterSerializer(serializers.Serializer):
    import_id = serializers.IntegerField(required=False, min_value=1)


class ImportedTeacherListFilterSerializer(ImportFilterSerializer):
    search = serializers.CharField(required=False, allow_blank=True)


class ImportSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = ScheduleImport
        fields = ["id", "source_metadata", "created_at", "review_required"]


class TeacherRuleResponseSerializer(serializers.Serializer):
    teacher = ImportedTeacherSerializer()
    schedule_import = ImportSummarySerializer(allow_null=True)
    count = serializers.IntegerField()
    next = serializers.URLField(allow_null=True)
    previous = serializers.URLField(allow_null=True)
    results = ImportedLessonSerializer(many=True)


class ImportedTeacherViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ImportedTeacherSerializer
    permission_classes = []

    def get_queryset(self):
        queryset = Teacher.objects.filter(imported_lessons__isnull=False).distinct().order_by("full_name", "pk")
        if self.action == "list":
            filters = ImportedTeacherListFilterSerializer(data=self.request.query_params)
            filters.is_valid(raise_exception=True)
            values = filters.validated_data
            if "import_id" in values:
                batch = get_object_or_404(ScheduleImport, pk=values["import_id"])
                queryset = queryset.filter(imported_lessons__group__schedule_import=batch)
            search = teacher_name(values.get("search", "")).casefold()
            if search:
                queryset = queryset.filter(dmami_name_key__contains=search)
        return queryset

    @extend_schema(parameters=[ImportedTeacherListFilterSerializer], description="Преподаватели опубликованных расписаний; search — поиск по ФИО.")
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(parameters=[ImportFilterSerializer], responses=TeacherRuleResponseSerializer,
                   description="Правила расписания преподавателя по ID. import_id выбирает снимок; по умолчанию последний импорт. Даты и week_raw сохраняются буквально, это не календарь событий.")
    @action(detail=True, methods=["get"])
    def schedule(self, request, pk=None):
        teacher = self.get_object()
        filters = ImportFilterSerializer(data=request.query_params)
        filters.is_valid(raise_exception=True)
        values = filters.validated_data
        batch = (get_object_or_404(ScheduleImport, pk=values["import_id"]) if "import_id" in values
                 else ScheduleImport.objects.order_by("-created_at", "-pk").first())
        lessons = (ImportedLesson.objects.filter(teachers=teacher, group__schedule_import=batch)
                   .select_related("group").prefetch_related("teachers")
                   .order_by("day_of_week", "lesson_number", "group__name", "date_from", "pk"))
        page = self.paginate_queryset(lessons)
        response = self.get_paginated_response(ImportedLessonSerializer(page, many=True).data)
        response.data = {"teacher": self.get_serializer(teacher).data,
                         "schedule_import": ImportSummarySerializer(batch).data if batch else None,
                         **response.data}
        return response
