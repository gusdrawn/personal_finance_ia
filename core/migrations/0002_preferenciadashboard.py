from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("core", "0001_initial")]
    operations = [migrations.CreateModel(
        name="PreferenciaDashboard",
        fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("orden_widgets", models.JSONField(blank=True, default=list)),
            ("updated_at", models.DateTimeField(auto_now=True)),
            ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="preferencia_dashboard", to="auth.user")),
        ],
    )]
