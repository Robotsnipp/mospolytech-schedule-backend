"""API каталога мероприятий: чтение открыто, добавление и импорт - администраторам."""

from datetime import date

from django.db.models import F
from django.db.models.functions import Coalesce
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from schedule_generator.api.permissions import IsAdminOrReadOnly

from .free_periods import (
    MAX_DAYS,
    MAX_GROUPS,
    FreePeriodsError,
    find_free_periods,
    resolve_groups,
)
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


FREE_PERIODS_PARAMETERS = [
    OpenApiParameter("groups", OpenApiTypes.STR, required=True,
                     description=f"Названия групп через запятую (до {MAX_GROUPS}), например 231-333,231-334"),
    OpenApiParameter("date_from", OpenApiTypes.DATE, required=True, description="Первый день поиска"),
    OpenApiParameter("date_to", OpenApiTypes.DATE, required=True,
                     description=f"Последний день поиска (диапазон до {MAX_DAYS} дней)"),
    OpenApiParameter("min_free", OpenApiTypes.INT,
                     description="Минимум свободных групп в периоде (по умолчанию 1)"),
    OpenApiParameter("limit", OpenApiTypes.INT, description="Сколько периодов вернуть (1-200, по умолчанию 20)"),
    OpenApiParameter("include_sunday", OpenApiTypes.BOOL, description="Учитывать воскресенья (1)"),
]


def _int_param(params, name, default):
    value = params.get(name)
    if value in (None, ""):
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValidationError({name: "Ожидается число."}) from exc


@extend_schema(summary="Самые свободные периоды для набора групп", tags=["Мероприятия"],
               parameters=FREE_PERIODS_PARAMETERS, responses={200: OpenApiTypes.OBJECT})
@api_view(["GET"])
@permission_classes([])
def free_periods(request):
    """Когда больше всего выбранных групп свободны - для планирования мероприятия.

    Занятость - реальное расписание rasp.dmami.ru. Соседние пары одного дня с
    одинаковым набором занятых групп склеиваются в один период.
    """
    params = request.query_params
    date_from = _date_param(params, "date_from")
    date_to = _date_param(params, "date_to")
    if date_from is None or date_to is None:
        raise ValidationError({"detail": "Нужны date_from и date_to в формате ГГГГ-ММ-ДД."})
    min_free = _int_param(params, "min_free", 1)
    limit = _int_param(params, "limit", 20)
    include_sunday = params.get("include_sunday") in ("1", "true", "True")
    try:
        groups = resolve_groups(params.get("groups", "").split(","))
        periods = find_free_periods(groups, date_from, date_to, include_sunday=include_sunday,
                                    min_free=min_free, limit=limit)
    except FreePeriodsError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    return Response({
        "groups": [g.title for g in groups],
        "date_from": date_from, "date_to": date_to,
        "periods": [p.as_dict() for p in periods],
    })
