"""URL-конфиг генератора расписания.

Карта фичи: URL -> ViewSet (api/views.py) -> сервис (services/).

Маршруты:
- POST /api/schedule/generate/   — action viewset'а GenerateViewSet,
  зарегистрированного в роутере под базовым префиксом "generate"
  (url_path="generate" даёт ровно "/generate/", без повтора сегмента);
- GET /api/schedule/runs/        — список запусков (ReadOnlyModelViewSet);
- GET /api/schedule/runs/<id>/   — результат конкретного запуска.
"""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .api.views import GenerateViewSet, GenerationRunViewSet

router = DefaultRouter()
# url_path="generate" у action сам по себе даёт "/{prefix}/generate/",
# поэтому префикс здесь — "" (корень include'а), чтобы итоговый URL остался
# ровно /api/schedule/generate/ (совместимость с прежним APIView-маршрутом).
router.register("", GenerateViewSet, basename="schedule-generate")
router.register("runs", GenerationRunViewSet, basename="schedule-run")

urlpatterns = [
    path("", include(router.urls)),
]
