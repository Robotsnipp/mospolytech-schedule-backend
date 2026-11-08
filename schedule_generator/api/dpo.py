"""API программ ДПО: программы, дисциплины, слушатели и планирование.

Чтение программ и дисциплин открыто, как у справочников; изменение, состав
слушателей и запуск планирования - только администраторам.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from ..models import DPODiscipline, DPOMembership, DPOProgram, GenerationRun
from ..services.dpo import DPOPlanningError, plan_program, prepare_program_load
from .views import CrudViewSet

TAG = ["Программы ДПО"]


class ModelCleanSerializer(serializers.ModelSerializer):
    """Проверяет инварианты модели (clean) до сохранения."""

    def validate(self, attrs):
        attrs = super().validate(attrs)
        m2m = {name: attrs.pop(name) for name in list(attrs)
               if self.Meta.model._meta.get_field(name).many_to_many}
        instance = self.Meta.model(**{**self._current_values(), **attrs})
        try:
            instance.clean()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(serializers.as_serializer_error(exc)) from exc
        attrs.update(m2m)
        return attrs

    def _current_values(self):
        if self.instance is None:
            return {}
        return {field.attname: getattr(self.instance, field.attname)
                for field in self.Meta.model._meta.concrete_fields}


class DPOProgramSerializer(ModelCleanSerializer):
    class Meta:
        model = DPOProgram
        fields = ["id", "name", "semester", "module", "weeks", "groups", "base_run", "description"]

    def validate_groups(self, groups):
        not_dpo = [group.name for group in groups if not group.is_dpo]
        if not_dpo:
            raise serializers.ValidationError("Это не группы ДПО: " + ", ".join(not_dpo))
        return groups


class DPODisciplineSerializer(ModelCleanSerializer):
    pairs_total = serializers.IntegerField(read_only=True)

    class Meta:
        model = DPODiscipline
        fields = ["id", "program", "subject", "teacher", "format", "total_hours", "total_pairs",
                  "pairs_total"]


class DPOMembershipSerializer(ModelCleanSerializer):
    class Meta:
        model = DPOMembership
        fields = ["id", "group", "student"]


class PlanRequestSerializer(serializers.Serializer):
    time_limit_seconds = serializers.IntegerField(min_value=1, max_value=300, default=30)
    seed = serializers.IntegerField(default=17)


class PlanResultSerializer(serializers.Serializer):
    run_id = serializers.IntegerField()
    status = serializers.CharField()
    scheduled_count = serializers.IntegerField(help_text="Размещено пар в неделю")
    required_count = serializers.IntegerField(help_text="Требовалось пар в неделю")
    issue_count = serializers.IntegerField()
    base_run_id = serializers.IntegerField(allow_null=True)
    other_dpo_run_ids = serializers.ListField(child=serializers.IntegerField())
    background_classes = serializers.IntegerField(help_text="Сколько пар учтено как занятость")


class DPORunSerializer(serializers.ModelSerializer):
    class Meta:
        model = GenerationRun
        fields = ["id", "status", "created_at", "message", "module"]


@extend_schema_view(
    list=extend_schema(summary="Список программ ДПО", tags=TAG),
    retrieve=extend_schema(summary="Программа ДПО", tags=TAG),
    create=extend_schema(summary="Создать программу ДПО", tags=TAG),
    update=extend_schema(summary="Обновить программу ДПО", tags=TAG),
    partial_update=extend_schema(summary="Частично обновить программу ДПО", tags=TAG),
    destroy=extend_schema(summary="Удалить программу ДПО", tags=TAG),
)
class DPOProgramViewSet(CrudViewSet):
    queryset = DPOProgram.objects.prefetch_related("groups")
    serializer_class = DPOProgramSerializer
    public_read = True

    @extend_schema(summary="Подготовить нагрузку программы (пары в неделю)", tags=TAG, request=None,
                   responses={200: serializers.ListSerializer(child=serializers.DictField())})
    @action(detail=True, methods=["post"], permission_classes=[IsAdminUser])
    def prepare(self, request, pk=None):
        try:
            events = prepare_program_load(self.get_object())
        except (DPOPlanningError, DjangoValidationError) as exc:
            return Response({"detail": _message(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response([{"event_id": event.pk, "group": event.group.name,
                          "subject": event.subject.name, "pairs_per_week": event.pairs_per_week}
                         for event in events])

    @extend_schema(summary="Спланировать программу поверх основного расписания", tags=TAG,
                   request=PlanRequestSerializer, responses={201: PlanResultSerializer})
    @action(detail=True, methods=["post"], permission_classes=[IsAdminUser])
    def plan(self, request, pk=None):
        params = PlanRequestSerializer(data=request.data)
        params.is_valid(raise_exception=True)
        try:
            result = plan_program(self.get_object(), **params.validated_data)
        except (DPOPlanningError, DjangoValidationError) as exc:
            return Response({"detail": _message(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(PlanResultSerializer(result).data, status=status.HTTP_201_CREATED)

    @extend_schema(summary="Запуски планирования программы", tags=TAG,
                   responses={200: DPORunSerializer(many=True)})
    @action(detail=True, methods=["get"])
    def runs(self, request, pk=None):
        runs = self.get_object().runs.order_by("-created_at", "-pk")
        return Response(DPORunSerializer(runs, many=True).data)


@extend_schema_view(
    list=extend_schema(summary="Дисциплины программ ДПО", tags=TAG),
    retrieve=extend_schema(summary="Дисциплина ДПО", tags=TAG),
    create=extend_schema(summary="Добавить дисциплину ДПО", tags=TAG),
    update=extend_schema(summary="Обновить дисциплину ДПО", tags=TAG),
    partial_update=extend_schema(summary="Частично обновить дисциплину ДПО", tags=TAG),
    destroy=extend_schema(summary="Удалить дисциплину ДПО", tags=TAG),
)
class DPODisciplineViewSet(CrudViewSet):
    queryset = DPODiscipline.objects.select_related("program", "subject", "teacher")
    serializer_class = DPODisciplineSerializer
    public_read = True


@extend_schema_view(
    list=extend_schema(summary="Слушатели групп ДПО (администратор)", tags=TAG),
    retrieve=extend_schema(summary="Слушатель ДПО", tags=TAG),
    create=extend_schema(summary="Включить студента в группу ДПО", tags=TAG),
    update=extend_schema(summary="Обновить запись слушателя", tags=TAG),
    partial_update=extend_schema(summary="Частично обновить запись слушателя", tags=TAG),
    destroy=extend_schema(summary="Исключить слушателя", tags=TAG),
)
class DPOMembershipViewSet(CrudViewSet):
    # Состав слушателей - персональные данные: только администраторам.
    queryset = DPOMembership.objects.select_related("group", "student")
    serializer_class = DPOMembershipSerializer


def _message(exc) -> str:
    if isinstance(exc, DjangoValidationError):
        return " ".join(exc.messages)
    return str(exc)
