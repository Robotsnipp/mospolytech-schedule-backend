from django.contrib import admin
from django.db.models.functions import Coalesce, ExtractYear

from .models import Event


class YearFilter(admin.SimpleListFilter):
    """Год по дате начала, а если её нет - по дате анонса."""

    title = "год"
    parameter_name = "year"

    def lookups(self, request, model_admin):
        years = (
            Event.objects.annotate(y=ExtractYear(Coalesce("date_start", "announced_at")))
            .exclude(y=None).values_list("y", flat=True).distinct().order_by("-y")
        )
        return [(str(y), str(y)) for y in years]

    def queryset(self, request, queryset):
        if self.value():
            return queryset.annotate(
                y=ExtractYear(Coalesce("date_start", "announced_at"))
            ).filter(y=self.value())
        return queryset


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ("title", "scope", "access", "date_start", "date_end",
                    "announced_at", "source", "is_published")
    list_filter = ("scope", "access", YearFilter, "is_published")
    list_editable = ("is_published",)
    search_fields = ("title",)
