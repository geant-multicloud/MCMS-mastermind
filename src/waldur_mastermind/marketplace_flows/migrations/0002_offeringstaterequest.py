# Generated by Django 2.2.24 on 2021-10-04 12:43

import django.db.models.deletion
import django.utils.timezone
import django_fsm
import model_utils.fields
from django.conf import settings
from django.db import migrations, models

import waldur_core.core.fields


class Migration(migrations.Migration):

    dependencies = [
        ('marketplace', '0059_offering_image'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('marketplace_flows', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='OfferingStateRequest',
            fields=[
                (
                    'id',
                    models.AutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name='ID',
                    ),
                ),
                (
                    'created',
                    model_utils.fields.AutoCreatedField(
                        default=django.utils.timezone.now,
                        editable=False,
                        verbose_name='created',
                    ),
                ),
                (
                    'modified',
                    model_utils.fields.AutoLastModifiedField(
                        default=django.utils.timezone.now,
                        editable=False,
                        verbose_name='modified',
                    ),
                ),
                ('uuid', waldur_core.core.fields.UUIDField()),
                (
                    'state',
                    django_fsm.FSMIntegerField(
                        choices=[
                            (1, 'draft'),
                            (2, 'pending'),
                            (3, 'approved'),
                            (4, 'rejected'),
                            (5, 'canceled'),
                        ],
                        default=1,
                    ),
                ),
                (
                    'reviewed_at',
                    models.DateTimeField(blank=True, editable=False, null=True),
                ),
                ('review_comment', models.TextField(blank=True, null=True)),
                (
                    'offering',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='+',
                        to='marketplace.Offering',
                    ),
                ),
                (
                    'requested_by',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='+',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    'reviewed_by',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='+',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={'abstract': False,},
        ),
    ]
