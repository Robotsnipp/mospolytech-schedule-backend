"""Persisted single-command generation of separate module weekly templates."""
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import serializers, status, viewsets
from rest_framework.response import Response

from ..models import Group, Semester, WeekPattern
from ..services.semester_generation import (
    SemesterGenerationError,
    generate_semester,
    list_semester_generations,
    read_semester_generation,
)
from .manual_variants import StrictSerializer


class SemesterGenerationRequestSerializer(StrictSerializer):
    semester = serializers.PrimaryKeyRelatedField(queryset=Semester.objects.all())
    group = serializers.PrimaryKeyRelatedField(queryset=Group.objects.all())
    week_pattern = serializers.PrimaryKeyRelatedField(queryset=WeekPattern.objects.all(), required=False, allow_null=True)
    resource_profile = serializers.CharField(max_length=120)
    settings = serializers.JSONField()
    clear_previous = serializers.BooleanField(required=False, default=False)

    def validate_clear_previous(self, value):
        if value:
            raise serializers.ValidationError('История сохраняется; clear_previous должен быть false.')
        return value


class SemesterGenerationFilterSerializer(StrictSerializer):
    semester = serializers.IntegerField(min_value=1, required=False)
    group = serializers.IntegerField(min_value=1, required=False)


@extend_schema_view(
    list=extend_schema(operation_id='schedule_semester_generations_list', responses=OpenApiTypes.OBJECT,
                       parameters=[SemesterGenerationFilterSerializer]),
    retrieve=extend_schema(operation_id='schedule_semester_generations_retrieve', responses=OpenApiTypes.OBJECT,
                           parameters=[OpenApiParameter('id', int, location=OpenApiParameter.PATH)]),
    create=extend_schema(request=SemesterGenerationRequestSerializer, responses={201: OpenApiTypes.OBJECT}),
)
class SemesterGenerationViewSet(viewsets.ViewSet):
    permission_classes: list = []

    def list(self, request):
        serializer = SemesterGenerationFilterSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        return Response({'results': list_semester_generations(**serializer.validated_data)})

    def retrieve(self, request, pk=None):
        batch_id = serializers.IntegerField(min_value=1).run_validation(pk)
        return Response(read_semester_generation(batch_id))

    def create(self, request):
        serializer = SemesterGenerationRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data.copy()
        data.pop('clear_previous', None)
        try:
            result = generate_semester(**data)
        except SemesterGenerationError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(result, status=status.HTTP_201_CREATED)
