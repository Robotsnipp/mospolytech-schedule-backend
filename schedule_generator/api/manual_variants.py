"""Thin HTTP boundary for immutable manual schedule alternatives."""

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from ..services.manual_variants import (
    ManualVariantError,
    list_variants,
    preview_variant,
    read_variant,
    save_variant,
    suggest_rooms,
)


class StrictSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        if not isinstance(data, dict):
            raise serializers.ValidationError("Ожидается объект.")
        unknown = set(data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError({key: "Неизвестное поле." for key in sorted(unknown)})
        return super().to_internal_value(data)


class PlacementChangeSerializer(StrictSerializer):
    class_id = serializers.IntegerField(min_value=1)
    slot_id = serializers.IntegerField(required=False, min_value=1)
    room_id = serializers.IntegerField(required=False, allow_null=True, min_value=1)

    def validate(self, attrs):
        if not (set(attrs) & {"slot_id", "room_id"}):
            raise serializers.ValidationError("Укажите новое время или аудиторию пары.")
        return attrs


class ManualVariantRequestSerializer(StrictSerializer):
    run_id = serializers.IntegerField(min_value=1)
    variant_id = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    changes = PlacementChangeSerializer(many=True, required=False, default=list)
    name = serializers.CharField(required=False, max_length=120, allow_blank=False)


class RoomSuggestionRequestSerializer(ManualVariantRequestSerializer):
    class_id = serializers.IntegerField(min_value=1)


@extend_schema_view(
    list=extend_schema(operation_id="schedule_manual_variants_list", responses=OpenApiTypes.OBJECT,
                       parameters=[OpenApiParameter("run_id", int, required=True)]),
    retrieve=extend_schema(operation_id="schedule_manual_variants_retrieve", responses=OpenApiTypes.OBJECT,
                           parameters=[OpenApiParameter("id", int, location=OpenApiParameter.PATH)]),
    create=extend_schema(request=ManualVariantRequestSerializer, responses={201: OpenApiTypes.OBJECT}),
)
class ManualVariantViewSet(viewsets.ViewSet):
    # Same policy as generation; no new role is introduced by this screen.
    permission_classes: list = []

    def list(self, request):
        serializer = serializers.IntegerField(min_value=1)
        run_id = serializer.run_validation(request.query_params.get("run_id"))
        return Response({"results": list_variants(run_id)})

    def retrieve(self, request, pk=None):
        return Response(read_variant(pk))

    def _operate(self, request, operation, request_serializer=ManualVariantRequestSerializer, response_status=200):
        serializer = request_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            result = operation(**serializer.validated_data)
        except ManualVariantError as exc:
            return Response({"detail": str(exc), **({"validation": exc.validation} if exc.validation else {})},
                            status=status.HTTP_400_BAD_REQUEST)
        return Response(result, status=response_status)

    @extend_schema(request=ManualVariantRequestSerializer, responses=OpenApiTypes.OBJECT)
    @action(detail=False, methods=["post"])
    def preview(self, request):
        return self._operate(request, preview_variant)

    @extend_schema(request=RoomSuggestionRequestSerializer, responses=OpenApiTypes.OBJECT)
    @action(detail=False, methods=["post"])
    def rooms(self, request):
        return self._operate(request, suggest_rooms, RoomSuggestionRequestSerializer)

    def create(self, request):
        return self._operate(request, save_variant, response_status=status.HTTP_201_CREATED)
