from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("parts", "0005_partquoterequest_prepared_quote"),
    ]

    operations = [
        migrations.AddField(
            model_name="partquoterequest",
            name="request_type",
            field=models.CharField(
                choices=[
                    ("manual_search", "Manual part search"),
                    ("weight_price", "Missing weight price preparation"),
                ],
                db_index=True,
                default="manual_search",
                max_length=30,
            ),
        ),
    ]
