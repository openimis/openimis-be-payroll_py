# Generated by Django 3.2.19 on 2023-08-09 10:57

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('payroll', '0005_change_i_user_to_user'),
    ]

    operations = [
        migrations.AddField(
            model_name='historicalpayroll',
            name='status',
            field=models.CharField(choices=[('CREATED', 'CREATED'), ('ONGOING', 'ONGOING'), ('AWAITING_FOR_RECONCILIATION', 'AWAITING_FOR_RECONCILIATION'), ('RECONCILIATED', 'RECONCILIATED')], default='CREATED', max_length=100),
        ),
        migrations.AddField(
            model_name='payroll',
            name='status',
            field=models.CharField(choices=[('CREATED', 'CREATED'), ('ONGOING', 'ONGOING'), ('AWAITING_FOR_RECONCILIATION', 'AWAITING_FOR_RECONCILIATION'), ('RECONCILIATED', 'RECONCILIATED')], default='CREATED', max_length=100),
        ),
        migrations.AlterField(
            model_name='paymentpoint',
            name='ppm',
            field=models.ForeignKey(on_delete=django.db.models.deletion.DO_NOTHING, to='core.user'),
        ),
    ]
