# -*- coding: utf-8 -*-
# Generated by Django 1.11.18 on 2019-02-08 13:24
from __future__ import unicode_literals

import django.core.validators
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone
import django_fsm
import model_utils.fields
import re
import taggit.managers
import waldur_core.core.fields
import waldur_core.core.models
import waldur_core.logging.loggers


class Migration(migrations.Migration):

    dependencies = [
        ('taggit', '0002_auto_20150616_2121'),
        ('waldur_azure', '0011_sql_collation'),
    ]

    operations = [
        migrations.CreateModel(
            name='SecurityGroup',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created', model_utils.fields.AutoCreatedField(default=django.utils.timezone.now, editable=False, verbose_name='created')),
                ('modified', model_utils.fields.AutoLastModifiedField(default=django.utils.timezone.now, editable=False, verbose_name='modified')),
                ('description', models.CharField(blank=True, max_length=500, verbose_name='description')),
                ('uuid', waldur_core.core.fields.UUIDField()),
                ('error_message', models.TextField(blank=True)),
                ('runtime_state', models.CharField(blank=True, max_length=150, verbose_name='runtime state')),
                ('state', django_fsm.FSMIntegerField(choices=[(5, 'Creation Scheduled'), (6, 'Creating'), (1, 'Update Scheduled'), (2, 'Updating'), (7, 'Deletion Scheduled'), (8, 'Deleting'), (3, 'OK'), (4, 'Erred')], default=5)),
                ('backend_id', models.CharField(blank=True, max_length=255)),
                ('name', models.CharField(max_length=80, validators=[django.core.validators.RegexValidator(message='The name can contain only letters, numbers, underscore, period and hyphens.', regex=re.compile('^[a-zA-Z][a-zA-Z0-9._-]+$'))])),
                ('resource_group', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='waldur_azure.ResourceGroup')),
                ('service_project_link', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='waldur_azure.AzureServiceProjectLink')),
                ('tags', taggit.managers.TaggableManager(related_name='+', blank=True, help_text='A comma-separated list of tags.', through='taggit.TaggedItem', to='taggit.Tag', verbose_name='Tags')),
            ],
            options={
                'abstract': False,
            },
            bases=(waldur_core.core.models.DescendantMixin, waldur_core.core.models.BackendModelMixin, waldur_core.logging.loggers.LoggableMixin, models.Model),
        ),
        migrations.AddField(
            model_name='networkinterface',
            name='security_group',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='waldur_azure.SecurityGroup'),
        ),
    ]
