"""Корневые маршруты проекта.

/api/schedule/  генератор расписания (schedule_generator)
/api/rasp/      реальное расписание rasp.dmami.ru (rasp)
/api/events/    каталог мероприятий и свободные периоды (events)
/api/docs/      Swagger, /api/schema/ - OpenAPI-схема
/admin/         админка Django, /health - проверка живости
/__dev/         dev-панель (только при DEBUG=True)
"""
from django.conf import settings
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularSwaggerView,
)


def health(request):
    return JsonResponse({'status': 'ok'})


urlpatterns = [
    path('health', health, name='health'),
    path('health/', health),
    path('admin/', admin.site.urls),
    path('api/schedule/', include('schedule_generator.urls')),
    path('api/rasp/', include('rasp.urls')),
    path('api/events/', include('events.urls')),
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'),
         name='swagger-ui'),
]

# Dev-панель — только в режиме разработки (приложение добавлено в
# INSTALLED_APPS при DEBUG=True, см. settings.py). В продакшене URL нет.
if settings.DEBUG:
    urlpatterns.append(path('__dev/', include('dev_dashboard.urls')))
