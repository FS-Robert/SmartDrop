from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('App', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='usuario',
            name='supabase_id',
            field=models.BigIntegerField(
                blank=True,
                db_column='id_usuario_supabase',
                null=True,
                unique=True,
            ),
        ),
    ]