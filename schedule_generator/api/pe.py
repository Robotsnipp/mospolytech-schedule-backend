"""API физкультуры: направления, слоты и записи студентов."""

from __future__ import annotations

from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.response import Response

from ..models import PEDirection, PEEnrollment, PESlot
from ..services.pe import PEEnrollmentError, check_can_enroll


class PEDirectionSerializer(serializers.ModelSerializer):
    class Meta:
        model = PEDirection
        fields = ["id", "code", "name", "icon"]


class PESlotSerializer(serializers.ModelSerializer):
    direction_code = serializers.CharField(source="direction.code", read_only=True)

    class Meta:
        model = PESlot
        fields = ["id", "direction", "direction_code", "day_of_week",
                  "start_time", "end_time", "place"]


class PEEnrollmentSerializer(serializers.ModelSerializer):
    slot_detail = PESlotSerializer(source="slot", read_only=True)

    class Meta:
        model = PEEnrollment
        fields = ["id", "owner", "group_title", "slot", "slot_detail"]
        extra_kwargs = {"owner": {"min_length": 8}}

    def validate(self, attrs):
        try:
            check_can_enroll(attrs["owner"], attrs.get("group_title", ""), attrs["slot"])
        except PEEnrollmentError as exc:
            raise serializers.ValidationError({"detail": str(exc)}) from exc
        return attrs


@extend_schema_view(list=extend_schema(summary="Направления физкультуры", tags=["Физкультура"]))
class PEDirectionViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes: list = []
    queryset = PEDirection.objects.order_by("id")
    serializer_class = PEDirectionSerializer


@extend_schema_view(list=extend_schema(summary="Слоты физкультуры (?direction=<id>)",
                                       tags=["Физкультура"]))
class PESlotViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes: list = []
    queryset = PESlot.objects.select_related("direction").order_by(
        "day_of_week", "start_time", "id")
    serializer_class = PESlotSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        direction = self.request.query_params.get("direction")
        if direction and direction.isdigit():
            qs = qs.filter(direction_id=int(direction))
        return qs


@extend_schema_view(
    list=extend_schema(summary="Записи на физкультуру (?owner=<client id>)",
                       tags=["Физкультура"]),
    create=extend_schema(summary="Записаться на слот физкультуры", tags=["Физкультура"]),
    destroy=extend_schema(summary="Отменить запись", tags=["Физкультура"]),
)
class PEEnrollmentViewSet(mixins.ListModelMixin, mixins.CreateModelMixin,
                          mixins.DestroyModelMixin, viewsets.GenericViewSet):
    """Запись/отмена открыты студентам без входа: в приложении нет аутентификации."""

    permission_classes: list = []
    queryset = PEEnrollment.objects.select_related("slot", "slot__direction").order_by("id")
    serializer_class = PEEnrollmentSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        owner = self.request.query_params.get("owner")
        if self.action == "list":
            qs = qs.filter(owner=owner) if owner else qs.none()
        return qs

    def destroy(self, request, *args, **kwargs):
        obj = self.get_object()
        if obj.owner != request.query_params.get("owner"):
            return Response({"detail": "Чужая запись."}, status=status.HTTP_403_FORBIDDEN)
        obj.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)
