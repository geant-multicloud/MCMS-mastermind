# -*- coding: utf-8 -*-
# Generated by Django 1.11.3 on 2017-08-17 12:00
from __future__ import unicode_literals

import django.core.validators
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone
import django_fsm
import model_utils.fields
import waldur_core.core.fields
import waldur_core.core.models
import waldur_core.core.validators
import waldur_core.logging.loggers
import taggit.managers


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('taggit', '0002_auto_20150616_2121'),
        ('structure', '0001_squashed_0054'),
    ]

    operations = [
        migrations.CreateModel(
            name='AzureService',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('uuid', waldur_core.core.fields.UUIDField()),
                ('available_for_all', models.BooleanField(default=False, help_text='Service will be automatically added to all customers projects if it is available for all')),
                ('customer', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='structure.Customer', verbose_name='organization')),
            ],
            options={
                'abstract': False,
            },
            bases=(waldur_core.core.models.DescendantMixin, waldur_core.logging.loggers.LoggableMixin, models.Model),
        ),
        migrations.CreateModel(
            name='AzureServiceProjectLink',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('cloud_service_name', models.CharField(blank=True, max_length=255)),
                ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='structure.Project')),
                ('service', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='waldur_azure.AzureService')),
            ],
            options={
                'abstract': False,
            },
            bases=(waldur_core.core.models.DescendantMixin, waldur_core.logging.loggers.LoggableMixin, models.Model),
        ),
        migrations.CreateModel(
            name='Image',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=150, validators=[waldur_core.core.validators.validate_name], verbose_name='name')),
                ('uuid', waldur_core.core.fields.UUIDField()),
                ('backend_id', models.CharField(max_length=255, unique=True)),
            ],
            options={
                'abstract': False,
            },
            bases=(waldur_core.core.models.BackendModelMixin, models.Model),
        ),
        migrations.CreateModel(
            name='InstanceEndpoint',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('local_port', models.IntegerField(validators=[django.core.validators.MaxValueValidator(65535)])),
                ('public_port', models.IntegerField(validators=[django.core.validators.MaxValueValidator(65535)])),
                ('protocol', models.CharField(blank=True, choices=[('tcp', 'tcp'), ('udp', 'udp')], max_length=3)),
                ('name', models.CharField(blank=True, choices=[('SSH', 'SSH'), ('Remote Desktop', 'Remote Desktop')], max_length=255)),
            ],
            bases=(waldur_core.core.models.BackendModelMixin, models.Model),
        ),
        migrations.CreateModel(
            name='VirtualMachine',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created', model_utils.fields.AutoCreatedField(default=django.utils.timezone.now, editable=False, verbose_name='created')),
                ('modified', model_utils.fields.AutoLastModifiedField(default=django.utils.timezone.now, editable=False, verbose_name='modified')),
                ('description', models.CharField(blank=True, max_length=500, verbose_name='description')),
                ('name', models.CharField(max_length=150, validators=[waldur_core.core.validators.validate_name], verbose_name='name')),
                ('uuid', waldur_core.core.fields.UUIDField()),
                ('error_message', models.TextField(blank=True)),
                ('latitude', models.FloatField(blank=True, null=True)),
                ('longitude', models.FloatField(blank=True, null=True)),
                ('runtime_state', models.CharField(blank=True, max_length=150, verbose_name='runtime state')),
                ('state', django_fsm.FSMIntegerField(choices=[(5, 'Creation Scheduled'), (6, 'Creating'), (1, 'Update Scheduled'), (2, 'Updating'), (7, 'Deletion Scheduled'), (8, 'Deleting'), (3, 'OK'), (4, 'Erred')], default=5)),
                ('backend_id', models.CharField(blank=True, max_length=255)),
                ('cores', models.PositiveSmallIntegerField(default=0, help_text='Number of cores in a VM')),
                ('ram', models.PositiveIntegerField(default=0, help_text='Memory size in MiB')),
                ('disk', models.PositiveIntegerField(default=0, help_text='Disk size in MiB')),
                ('min_ram', models.PositiveIntegerField(default=0, help_text='Minimum memory size in MiB')),
                ('min_disk', models.PositiveIntegerField(default=0, help_text='Minimum disk size in MiB')),
                ('image_name', models.CharField(blank=True, max_length=150)),
                ('key_name', models.CharField(blank=True, max_length=50)),
                ('key_fingerprint', models.CharField(blank=True, max_length=47)),
                ('user_data', models.TextField(blank=True, help_text='Additional data that will be added to instance on provisioning')),
                ('start_time', models.DateTimeField(blank=True, null=True)),
                ('public_ips', waldur_core.core.fields.JSONField(blank=True, default=[], help_text='List of public IP addresses')),
                ('private_ips', waldur_core.core.fields.JSONField(blank=True, default=[], help_text='List of private IP addresses')),
                ('user_username', models.CharField(max_length=50)),
                ('user_password', models.CharField(max_length=50)),
                ('service_project_link', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='virtualmachines', to='waldur_azure.AzureServiceProjectLink')),
                ('tags', taggit.managers.TaggableManager(related_name='+', blank=True, help_text='A comma-separated list of tags.', through='taggit.TaggedItem', to='taggit.Tag', verbose_name='Tags')),
            ],
            options={
                'abstract': False,
            },
            bases=(waldur_core.core.models.DescendantMixin, waldur_core.core.models.BackendModelMixin, waldur_core.logging.loggers.LoggableMixin, models.Model),
        ),
        migrations.AddField(
            model_name='instanceendpoint',
            name='instance',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='endpoints', to='waldur_azure.VirtualMachine'),
        ),
        migrations.AddField(
            model_name='azureservice',
            name='projects',
            field=models.ManyToManyField(related_name='azure_services', through='waldur_azure.AzureServiceProjectLink', to='structure.Project'),
        ),
        migrations.AddField(
            model_name='azureservice',
            name='settings',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='structure.ServiceSettings'),
        ),
        migrations.AlterUniqueTogether(
            name='azureserviceprojectlink',
            unique_together=set([('service', 'project')]),
        ),
        migrations.AlterUniqueTogether(
            name='azureservice',
            unique_together=set([('customer', 'settings')]),
        ),
    ]
