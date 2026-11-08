from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import EventViewSet, free_periods

router = DefaultRouter()
router.register("", EventViewSet, basename="event")

# free-periods/ стоит раньше маршрутов router, чтобы не совпасть с <id>/.
urlpatterns = [path("free-periods/", free_periods, name="event-free-periods"), *router.urls]
