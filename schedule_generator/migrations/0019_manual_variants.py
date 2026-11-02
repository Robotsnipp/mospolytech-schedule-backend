from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("schedule_generator", "0018_room_provenance")]

    operations = [
        migrations.CreateModel(
            name="ManualScheduleVariant",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(default="Ручной вариант", max_length=120)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("validation", models.JSONField(default=dict)),
                ("calendar_snapshot", models.JSONField(default=dict)),
                ("source_run", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="manual_variants", to="schedule_generator.generationrun")),
                ("parent_variant", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="subsequent_variants", to="schedule_generator.manualschedulevariant")),
            ],
            options={"ordering": ["-created_at", "-id"]},
        ),
        migrations.CreateModel(
            name="ManualPlacement",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("event_id", models.PositiveIntegerField()),
                ("slot_id", models.PositiveIntegerField()),
                ("room_id", models.PositiveIntegerField(blank=True, null=True)),
                ("calendar_row", models.JSONField(default=dict)),
                ("source_class", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="manual_placements", to="schedule_generator.scheduledclass")),
                ("variant", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="placements", to="schedule_generator.manualschedulevariant")),
            ],
            options={
                "ordering": ["source_class_id"],
                "constraints": [models.UniqueConstraint(fields=("variant", "source_class"), name="unique_manual_variant_source_class")],
            },
        ),
    ]
