from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("banca", "0018_importacion_documentos_municipales")]

    operations = [
        migrations.AddField(
            model_name="configuracionbanca",
            name="presupuesto_mensual_clp",
            field=models.DecimalField(
                max_digits=15, decimal_places=2, default=Decimal("0"),
                help_text="Límite mensual CLP para avisar sobre compras aún no facturadas; 0 desactiva el aviso.",
            ),
        ),
    ]
