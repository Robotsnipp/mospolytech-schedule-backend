"""Public read-only discovery, with the same visibility as schedule projections."""

from django.db.models import Count, Prefetch
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from ..models import GenerationRun, ScheduledClass
from ..services.schedule_read import with_schedule_details
from .crud import GenerationRunListSerializer, GenerationRunSerializer


class HistoryFilterSerializer(serializers.Serializer):
    semester = serializers.IntegerField(required=False, min_value=1)
    module = serializers.IntegerField(required=False, min_value=1)


@extend_schema(parameters=[HistoryFilterSerializer], responses=GenerationRunListSerializer(many=True))
@api_view(["GET"])
@permission_classes([AllowAny])
def generation_history(request):
    query = HistoryFilterSerializer(data=request.query_params)
    query.is_valid(raise_exception=True)
    runs = GenerationRun.objects.annotate(
        classes_count=Count("scheduled_classes", distinct=True), issues_count=Count("issues", distinct=True))
    if "semester" in query.validated_data:
        runs = runs.filter(semester_id=query.validated_data["semester"])
    if "module" in query.validated_data:
        runs = runs.filter(module_id=query.validated_data["module"])
    result = []
    for run in runs.order_by("-created_at", "-id"):
        item = dict(GenerationRunListSerializer(run).data)
        item.update(scheduled_count=run.classes_count, group_id=(run.input_snapshot or {}).get("group_id"),
                    editable=bool(run.settings_snapshot and run.input_snapshot))
        result.append(item)
    return Response(result)


@extend_schema(responses=GenerationRunSerializer)
@api_view(["GET"])
@permission_classes([AllowAny])
def generation_history_detail(request, run_id):
    run = get_object_or_404(GenerationRun.objects.prefetch_related(
        Prefetch("scheduled_classes", queryset=with_schedule_details(ScheduledClass.objects.all())),
        "issues"), pk=run_id)
    return Response(GenerationRunSerializer(run).data)
