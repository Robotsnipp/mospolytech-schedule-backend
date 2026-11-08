"""API каталога мероприятий: чтение открыто, добавление и импорт - администраторам."""

from datetime import date

from django.db.models import F
from django.db.models.functions import Coalesce
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from schedule_generator.api.permissions import IsAdminOrReadOnly

from .importer import EventDataError, import_events
from .models import Event
from .serializers import (
    EventImportRequestSerializer,
    EventImportResultSerializer,
    EventSerializer,
)

LIST_PARAMETERS = [
    OpenApiParameter("scope", OpenApiTypes.STR, enum=Event.Scope.values,
                     description="university - вуз, city - город"),
    OpenApiParameter("access", OpenApiTypes.STR, enum=Event.Access.values),
    OpenApiParameter("year", OpenApiTypes.INT,
                     description="Год по дате начала, а если её нет - по дате анонса"),
    OpenApiParameter("date_from", OpenApiTypes.DATE,
                     description="Не раньше этой даты (дата начала или анонса)"),
    OpenApiParameter("date_to", OpenApiTypes.DATE,
                     description="Не позже этой даты (дата начала или анонса)"),
    OpenApiParameter("q", OpenApiTypes.STR, description="Поиск по названию"),
]


def _date_param(params, name):
    value = params.get(name)
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError({name: "Ожидается дата в формате ГГГГ-ММ-ДД."}) from exc


@extend_schema_view(
    list=extend_schema(summary="Список опубликованных мероприятий", tags=["Мероприятия"],
                       parameters=LIST_PARAMETERS),
    retrieve=extend_schema(summary="Одно мероприятие", tags=["Мероприятия"]),
    create=extend_schema(summary="Добавить мероприятие (администратор)", tags=["Мероприятия"]),
)
class EventViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin,
                   mixins.CreateModelMixin, viewsets.GenericViewSet):
    serializer_class = EventSerializer
    permission_classes = [IsAdminOrReadOnly]

    def get_queryset(self):
        qs = Event.objects.filter(is_published=True).annotate(
            sort_date=Coalesce("date_start", "announced_at"),
        )
        if self.action != "list":
            return qs
        params = self.request.query_params
        if params.get("scope"):
            qs = qs.filter(scope=params["scope"])
        if params.get("access"):
            qs = qs.filter(access=params["access"])
        if params.get("year"):
            try:
                year = int(params["year"])
            except ValueError as exc:
                raise ValidationError({"year": "Ожидается число."}) from exc
            qs = qs.filter(sort_date__year=year)
        date_from = _date_param(params, "date_from")
        if date_from:
            qs = qs.filter(sort_date__gte=date_from)
        date_to = _date_param(params, "date_to")
        if date_to:
            qs = qs.filter(sort_date__lte=date_to)
        query = params.get("q", "").strip().casefold()
        if query:
            # SQLite не сравнивает кириллицу без учёта регистра (LIKE/icontains),
            # поэтому сравниваем в Python. Каталог небольшой, это дёшево.
            ids = [pk for pk, title in qs.values_list("id", "title")
                   if query in title.casefold()]
            qs = qs.filter(id__in=ids)
        return qs.order_by(F("sort_date").asc(nulls_last=True), "id")

    @extend_schema(summary="Импортировать мероприятия из JSON (администратор)",
                   tags=["Мероприятия"], request=EventImportRequestSerializer,
                   responses={200: EventImportResultSerializer})
    @action(detail=False, methods=["post"], url_path="import",
            permission_classes=[IsAdminUser])
    def import_file(self, request):
        try:
            result = import_events(request.data)
        except EventDataError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(result)
