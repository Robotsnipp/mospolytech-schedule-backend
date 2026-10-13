"""HTTP-представление фичи «генерация расписания» (DRF ViewSets).

Здесь ТОЛЬКО протокол: разбор/валидация запроса -> вызов сервисного слоя
(services/generation.run_generation) -> сериализация ответа. Никакой
бизнес-логики и работы с решателем — всё это видно в services/.

Эндпоинты (см. urls.py, маршрутизация через DefaultRouter):
- POST /api/schedule/generate/   — запустить генерацию, вернуть GenerationRun;
- GET  /api/schedule/runs/       — список запусков генерации;
- GET  /api/schedule/runs/<id>/  — посмотреть результат запуска.
"""

from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from ..models import GenerationRun
from ..services import GenerationError, run_generation
from .serializers import GenerateRequestSerializer, GenerationRunSerializer


class GenerateViewSet(viewsets.ViewSet):
    """Запуск генерации расписания (OR-Tools CP-SAT).

    POST /api/schedule/generate/ (action generate, см. url_path ниже).

    Что происходит внутри (детали — в services/generation.py):
    1. Берутся все LessonEvent семестра; физкультура (PE) исключается —
       она не генерируется (студент приходит в любое время/место).
    2. Лекции планируются онлайн на потоки групп (один слот на поток),
       практики/лаборатории — очно на группу, ПД — очно на студентов,
       выбравших проект, внеучебка — очно или онлайн (поле format).
    3. CP-SAT размещает требуемые пары по слотам шаблона недели, выбирает
       аудитории (вместимость >= размер аудитории), минимизирует окна и
       перегрузку преподавателей.
    4. Результат сохраняется как ScheduledClass + GenerationRun (+ Issues).
    """

    permission_classes: list = []
    @extend_schema(
        request=GenerateRequestSerializer,
        responses={status.HTTP_201_CREATED: GenerationRunSerializer}
    )
    @action(detail=False, methods=["post"], url_path="generate")
    def generate(self, request):
        serializer = GenerateRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            outcome = run_generation(**serializer.validated_data)
        except GenerationError as exc:
            return Response({"detail": str(exc)},
                            status=status.HTTP_400_BAD_REQUEST)
        run = GenerationRun.objects.get(pk=outcome.run_id)
        return Response(GenerationRunSerializer(run).data,
                        status=status.HTTP_201_CREATED)


class GenerationRunViewSet(viewsets.ReadOnlyModelViewSet):
    """Запуски генерации: GET /api/schedule/runs/ и GET /api/schedule/runs/<id>/."""

    queryset = GenerationRun.objects.all()
    serializer_class = GenerationRunSerializer
    permission_classes: list = []
