# Generated manually for alternative part proposals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0010_payment"),
    ]

    operations = [
        migrations.AddField(
            model_name="orderitem",
            name="proposed_part_number",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="orderitem",
            name="proposed_name",
            field=models.CharField(blank=True, max_length=255),
        ),
    ]
