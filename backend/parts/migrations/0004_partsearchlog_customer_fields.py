from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("parts", "0003_partsearchlog"),
    ]

    operations = [
        migrations.AddField(
            model_name="partsearchlog",
            name="customer_phone",
            field=models.CharField(blank=True, db_index=True, max_length=50),
        ),
        migrations.AddField(
            model_name="partsearchlog",
            name="customer_name",
            field=models.CharField(blank=True, max_length=150),
        ),
    ]
