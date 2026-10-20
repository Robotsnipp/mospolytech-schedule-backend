"""Пагинация с настраиваемым размером страницы (?page_size=, не более 1000)."""

from rest_framework.pagination import PageNumberPagination


class FlexiblePagination(PageNumberPagination):
    page_size_query_param = "page_size"
    max_page_size = 1000
