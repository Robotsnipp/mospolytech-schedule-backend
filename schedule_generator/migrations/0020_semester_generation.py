import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('schedule_generator', '0019_manual_variants')]
    operations = [
        migrations.CreateModel(name='SemesterGeneration', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('resource_profile', models.CharField(max_length=120)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('status', models.CharField(default='PENDING', max_length=20)),
            ('settings_snapshot', models.JSONField(default=dict)),
            ('input_snapshot', models.JSONField(default=dict)),
            ('module_results', models.JSONField(default=list)),
            ('solve_time_seconds', models.FloatField(default=0)),
            ('elapsed_seconds', models.FloatField(default=0)),
            ('message', models.TextField(blank=True)),
            ('semester', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='semester_generations', to='schedule_generator.semester')),
            ('group', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='semester_generations', to='schedule_generator.group')),
            ('week_pattern', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='semester_generations', to='schedule_generator.weekpattern')),
        ], options={'ordering': ['-created_at', '-id']}),
        migrations.CreateModel(name='SemesterGenerationModule', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('budget_seconds', models.PositiveSmallIntegerField()),
            ('result', models.JSONField(default=dict)),
            ('batch', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='module_runs', to='schedule_generator.semestergeneration')),
            ('module', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='semester_generations', to='schedule_generator.module')),
            ('run', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='semester_generations', to='schedule_generator.generationrun')),
        ], options={'ordering': ['id'], 'constraints': [models.UniqueConstraint(fields=('batch', 'module'), name='unique_semester_generation_module')]}),
    ]
