"""Real module solves, immutable semester history and honest hour totals."""
from datetime import date, time
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from rest_framework.test import APIClient

from .models import (
    GenerationRun,
    Group,
    LessonEvent,
    Module,
    ResourceProfile,
    Room,
    Semester,
    SemesterGeneration,
    Student,
    Subject,
    Teacher,
    TimeSlot,
    WeekPattern,
)
from .services.generation import GenerationError, run_generation
from .services.semester_generation import generate_semester, read_semester_generation


class SemesterGenerationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.semester = Semester.objects.create(name='Семестр целиком',start_date=date(2026,10,19),weeks_count=4)
        self.group = Group.objects.create(name='251-8899',semester=self.semester)
        Student.objects.create(group=self.group,full_name='Аноним 01')
        self.teacher = Teacher.objects.create(full_name='Преподаватель демо')
        self.pattern = WeekPattern.objects.create(semester=self.semester,name='Недельная сетка')
        for day in range(7):
            for number in range(1,3):
                TimeSlot.objects.create(week_pattern=self.pattern,day_of_week=day,lesson_number=number,
                    start_time=time(7+number*2),end_time=time(8+number*2))
        self.room = Room.objects.create(name='Пр2222',confirmed_capacity=None)
        self.profile = ResourceProfile.objects.create(key='semester-test',name='Помещения примера',semester=self.semester,
            group=self.group,rooms=[{'room_id':self.room.pk,'capacity_assumption':30}])
        self.modules = [
            Module.objects.create(semester=self.semester,number=1,start_date=date(2026,10,19),end_date=date(2026,10,25),teaching_weeks=1),
            Module.objects.create(semester=self.semester,number=2,start_date=date(2026,10,26),end_date=date(2026,11,8),teaching_weeks=2),
        ]
        self.events = [LessonEvent.objects.create(semester=self.semester,module=module,group=self.group,
            teacher=self.teacher,subject=Subject.objects.create(name=f'Предмет {module.number}'),lesson_type='PRACTICE',
            pairs_per_week=pairs,academic_hours=4,audience_size=1)
            for module,pairs in zip(self.modules,[2,1],strict=True)]
        self.settings = {'schema_version':1,'rules':{},'search':{'time_limit_seconds':5,'seed':17}}
        self.payload = {'semester':self.semester.pk,'group':self.group.pk,'week_pattern':self.pattern.pk,
                        'resource_profile':self.profile.key,'settings':self.settings,'clear_previous':False}

    def generate(self):
        return generate_semester(semester=self.semester,group=self.group,week_pattern=self.pattern,
            resource_profile=self.profile.key,settings=self.settings)

    def test_real_solver_uses_two_distinct_module_loads_and_preserves_source(self):
        old = GenerationRun.objects.create(semester=self.semester,status='SOLVED',message='Исторический результат')
        before = list(LessonEvent.objects.values().order_by('id'))
        response = self.client.post('/api/schedule/semester-generations/',self.payload,format='json')
        self.assertEqual(response.status_code,201,response.data)
        data=response.data
        self.assertEqual(data['status'],'SOLVED')
        self.assertTrue(data['complete'])
        self.assertEqual(data['required_hours'],8)
        self.assertEqual(data['scheduled_hours'],8)
        self.assertEqual(data['scheduled_count'],3)
        self.assertEqual([item['weekly_required_pairs'] for item in data['modules']],[2,1])
        self.assertEqual([item['weekly_scheduled_pairs'] for item in data['modules']],[2,1])
        self.assertEqual([item['budget_seconds'] for item in data['modules']],[3,2])
        batch=SemesterGeneration.objects.get(pk=data['id'])
        self.assertEqual(batch.module_runs.count(),2)
        self.assertEqual({child.run_id for child in batch.module_runs.all()}, {row['run_id'] for row in data['modules']})
        for row in data['modules']:
            run=GenerationRun.objects.get(pk=row['run_id'])
            self.assertEqual(run.module_id,row['module_id'])
            self.assertEqual(run.settings_snapshot['search']['time_limit_seconds'],row['budget_seconds'])
            self.assertTrue(run.validation_snapshot['valid'])
        self.assertEqual(list(LessonEvent.objects.values().order_by('id')),before)
        old.refresh_from_db()
        self.assertEqual(old.message,'Исторический результат')
        self.assertEqual(self.client.get(f'/api/schedule/semester-generations/{batch.pk}/').data,data)
        history=self.client.get('/api/schedule/semester-generations/',{'semester':self.semester.pk,'group':self.group.pk})
        self.assertEqual([item['id'] for item in history.data['results']],[batch.pk])
        empty=self.client.get('/api/schedule/semester-generations/',{'group':self.group.pk+100})
        self.assertEqual(empty.data['results'],[])

    def test_missing_module_load_rejects_before_creating_any_history(self):
        self.events[1].delete()
        response=self.client.post('/api/schedule/semester-generations/',self.payload,format='json')
        self.assertEqual(response.status_code,400,response.data)
        self.assertIn('Модуль 2',response.data['detail'])
        self.assertEqual(SemesterGeneration.objects.count(),0)
        self.assertEqual(GenerationRun.objects.count(),0)

    def test_overlapping_module_dates_and_too_small_budget_reject(self):
        self.modules[1].start_date=self.modules[0].end_date
        self.modules[1].save()
        response=self.client.post('/api/schedule/semester-generations/',self.payload,format='json')
        self.assertEqual(response.status_code,400)
        self.assertIn('пересекаются',response.data['detail'])
        self.modules[1].start_date=date(2026,10,26)
        self.modules[1].save()
        self.payload['settings']['search']['time_limit_seconds']=1
        response=self.client.post('/api/schedule/semester-generations/',self.payload,format='json')
        self.assertEqual(response.status_code,400)
        self.assertEqual(SemesterGeneration.objects.count(),0)

    def test_common_semester_hours_are_never_copied_as_per_module_hours(self):
        LessonEvent.objects.create(semester=self.semester,group=self.group,teacher=self.teacher,
            subject=Subject.objects.create(name='Общий предмет'),lesson_type='PRACTICE',pairs_per_week=1,
            academic_hours=12,audience_size=1)
        data=self.generate()
        self.assertEqual(data['status'],'SOLVED')
        self.assertFalse(data['hours_complete'])
        self.assertIsNone(data['required_hours'])
        self.assertIsNone(data['scheduled_hours'])
        self.assertEqual(data['known_required_hours'],8)
        self.assertEqual(data['known_scheduled_hours'],8)
        self.assertEqual([row['weekly_scheduled_pairs'] for row in data['modules']],[3,2])
        self.assertTrue(all(row['hours_unknown_reasons'] for row in data['modules']))

    def test_snapshot_reads_do_not_follow_edited_labels_and_dates(self):
        data=self.generate()
        self.semester.name='Изменённое название'
        self.semester.save()
        self.modules[0].end_date=date(2026,10,24)
        self.modules[0].save()
        self.events[0].academic_hours=100
        self.events[0].save()
        reopened=read_semester_generation(data['id'])
        self.assertEqual(reopened,data)

    def test_real_incomplete_load_is_reported_for_each_module(self):
        self.settings['rules']={'free_days':[{'day':day,'mode':'required'} for day in
                                             ['mon','tue','wed','thu','fri','sat','sun']]}
        data=self.generate()
        self.assertEqual(data['status'],'PARTIAL')
        self.assertFalse(data['complete'])
        self.assertEqual(data['scheduled_hours'],0)
        self.assertTrue(all(row['status']=='PARTIAL' for row in data['modules']))
        self.assertEqual(GenerationRun.objects.count(),2)

    def test_failed_child_is_linked_and_later_module_is_still_solved(self):
        original=run_generation
        def fail_first(**kwargs):
            if kwargs['module'].pk==self.modules[0].pk:
                failed=GenerationRun.objects.create(semester=self.semester,module=self.modules[0],status='ERROR',message='Ошибка входов')
                error=GenerationError('Ошибка входов')
                error.run_id=failed.pk
                raise error
            return original(**kwargs)
        with patch('schedule_generator.services.semester_generation.run_generation',side_effect=fail_first):
            data=self.generate()
        self.assertEqual(data['status'],'PARTIAL')
        self.assertEqual([row['status'] for row in data['modules']],['ERROR','SOLVED'])
        self.assertTrue(data['modules'][0]['run_id'])
        self.assertEqual(data['scheduled_hours'],4)
        self.assertEqual(SemesterGeneration.objects.get(pk=data['id']).module_runs.filter(run__isnull=False).count(),2)

    def test_api_rejects_single_module_and_old_parameters(self):
        for extra in [{'module':self.modules[0].pk},{'seed':42},{'clear_previous':True},{'unknown':1}]:
            response=self.client.post('/api/schedule/semester-generations/',{**self.payload,**extra},format='json')
            self.assertEqual(response.status_code,400,response.data)
        self.assertEqual(SemesterGeneration.objects.count(),0)

    def test_invalid_batch_references_do_not_fall_back_to_latest(self):
        self.generate()
        self.assertEqual(self.client.get('/api/schedule/semester-generations/99999/').status_code,404)
        self.assertEqual(self.client.get('/api/schedule/semester-generations/invalid/').status_code,400)

    def test_three_academic_hours_per_pair_are_frozen_and_counted(self):
        for event,module in zip(self.events,self.modules,strict=True):
            event.academic_hours=6
            event.prepared_for_group=self.group
            event.preparation_source={'academic_hours_per_pair':3,'teaching_weeks':module.teaching_weeks}
            event.save()
        data=self.generate()
        self.assertEqual(data['status'],'SOLVED')
        self.assertEqual((data['required_hours'],data['scheduled_hours']),(12,12))
        for module in data['input_snapshot']['modules']:
            self.assertEqual(module['events'][0]['hours_per_pair_ratio'],[3,1])
            self.assertEqual(module['events'][0]['hours_per_pair_source'],'preparation_source.academic_hours_per_pair')
        self.assertTrue(all(row['scheduled_hours']==6 for row in data['modules']))

    def test_explicit_hours_derive_exact_factor_instead_of_defaulting_to_two(self):
        for event in self.events:
            event.academic_hours=6
            event.save()
        data=self.generate()
        self.assertEqual((data['required_hours'],data['scheduled_hours']),(12,12))
        self.assertTrue(all(row['events'][0]['hours_per_pair_ratio']==[3,1]
                            for row in data['input_snapshot']['modules']))

    def test_coverage_records_gaps_without_inventing_semester_end(self):
        self.semester.weeks_count=1
        self.semester.save()
        self.modules[0].start_date=date(2026,10,20)
        self.modules[0].save()
        self.modules[1].start_date=date(2026,10,28)
        self.modules[1].save()
        data=self.generate()
        self.assertTrue(data['complete'])
        coverage=data['coverage']
        self.assertEqual(coverage['basis'],'configured_modules')
        self.assertEqual(coverage['start_date'],'2026-10-20')
        self.assertEqual(coverage['end_date'],'2026-11-08')
        self.assertIsNone(coverage['semester_end_date'])
        self.assertIsNone(coverage['calendar_complete'])
        self.assertTrue(coverage['trailing_gap_unknown'])
        self.assertFalse(coverage['starts_at_semester_start'])
        self.assertFalse(coverage['continuous_between_modules'])
        self.assertEqual([(row['start_date'],row['end_date'],row['kind']) for row in coverage['gaps']],
                         [('2026-10-19','2026-10-19','before_first_module'),
                          ('2026-10-26','2026-10-27','between_modules')])
        self.assertEqual(data['input_snapshot']['coverage'],coverage)

    def test_mid_command_load_change_preserves_actual_run_without_false_hour_total(self):
        original=run_generation
        def mutate_second(**kwargs):
            if kwargs['module'].pk==self.modules[1].pk:
                self.modules[1].teaching_weeks=1
                self.modules[1].save()
                self.events[1].academic_hours=2
                self.events[1].save()
            return original(**kwargs)
        with patch('schedule_generator.services.semester_generation.run_generation',side_effect=mutate_second):
            data=self.generate()
        self.assertEqual(data['status'],'PARTIAL')
        self.assertFalse(data['complete'])
        self.assertEqual([row['status'] for row in data['modules']],['SOLVED','ERROR'])
        changed=data['modules'][1]
        self.assertTrue(changed['input_changed'])
        self.assertEqual(changed['expected_required_hours'],4)
        self.assertEqual((changed['required_hours'],changed['scheduled_hours']),(2,2))
        self.assertEqual((data['required_hours'],data['scheduled_hours']),(6,6))
        self.assertIn('изменились',changed['message'])
        actual=GenerationRun.objects.get(pk=changed['run_id'])
        self.assertEqual(actual.status,'SOLVED')
        self.assertEqual(actual.input_snapshot['events'][0]['academic_hours'],2)
        self.assertEqual(actual.input_snapshot['module_teaching_weeks'],1)
        self.assertNotEqual(changed['expected_input_fingerprint'],changed['actual_input_fingerprint'])

    def test_room_source_change_between_modules_rejects_unstarted_child(self):
        original=run_generation
        def mutate_after_first(**kwargs):
            outcome=original(**kwargs)
            if kwargs['module'].pk==self.modules[0].pk:
                self.profile.rooms=[{'room_id':self.room.pk,'capacity_assumption':31}]
                self.profile.save()
            return outcome
        with patch('schedule_generator.services.semester_generation.run_generation',side_effect=mutate_after_first):
            data=self.generate()
        self.assertEqual(data['status'],'PARTIAL')
        self.assertFalse(data['complete'])
        self.assertEqual([row['status'] for row in data['modules']],['SOLVED','ERROR'])
        self.assertTrue(data['modules'][1]['input_changed'])
        self.assertIsNone(data['modules'][1]['run_id'])
        self.assertEqual(GenerationRun.objects.count(),1)

    def test_fixed_pd_uses_explicit_module_dates_beyond_teaching_week_duration(self):
        from .services.fixed_pd import occurrence_dates
        self.semester.weeks_count=1
        session=SimpleNamespace(day_of_week=0,date=None)
        dates=occurrence_dates(session,self.semester,self.modules[1],self.pattern)
        self.assertEqual(dates,frozenset({date(2026,10,26),date(2026,11,2)}))
        legacy=occurrence_dates(session,self.semester,None,self.pattern)
        self.assertEqual(legacy,frozenset({date(2026,10,19)}))
