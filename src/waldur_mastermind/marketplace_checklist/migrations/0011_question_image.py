# Generated by Django 2.2.24 on 2021-06-23 11:40

from django.db import migrations, models

import waldur_core.media.models


class Migration(migrations.Migration):

    dependencies = [
        ('marketplace_checklist', '0010_checklistcustomerrole_checklistprojectrole'),
    ]

    operations = [
        migrations.AddField(
            model_name='question',
            name='image',
            field=models.ImageField(
                blank=True,
                null=True,
                upload_to=waldur_core.media.models.get_upload_path,
            ),
        ),
    ]
