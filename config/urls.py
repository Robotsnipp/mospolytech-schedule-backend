"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
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
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'),
         name='swagger-ui'),
]

# Dev-панель — только в режиме разработки (приложение добавлено в
# INSTALLED_APPS при DEBUG=True, см. settings.py). В продакшене URL нет.
if settings.DEBUG:
    urlpatterns.append(path('__dev/', include('dev_dashboard.urls')))
