"""Приложение dev_dashboard: отладочный интерфейс проверки проекта.

Существует и подключается ТОЛЬКО при DEBUG=True (см. config/settings.py).
В продакшене app не импортируется вообще, поэтому его шаблоны/urls
недоступны даже при ошибочном включении DEBUG без явного подключения.
"""

from django.apps import AppConfig


class DevDashboardConfig(AppConfig):
    name = "dev_dashboard"
    verbose_name = "Dev-панель проверки"
