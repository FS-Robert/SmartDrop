from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ml_engine', '0005_sensorreading_home_ts'),
    ]

    operations = [
        migrations.AddField(
            model_name='sensorreading',
            name='motivo_invalido',
            field=models.CharField(blank=True, default='', max_length=120),
        ),
    ]
