import uuid

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("parts", "0004_partsearchlog_customer_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="partquoterequest",
            name="availability",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="partquoterequest",
            name="brand",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="partquoterequest",
            name="condition",
            field=models.CharField(blank=True, max_length=80),
        ),
        migrations.AddField(
            model_name="partquoterequest",
            name="currency",
            field=models.CharField(default="GEL", max_length=10),
        ),
        migrations.AddField(
            model_name="partquoterequest",
            name="eta_days",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="partquoterequest",
            name="final_price_gel",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name="partquoterequest",
            name="name",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="partquoterequest",
            name="notification_acknowledged_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="partquoterequest",
            name="notification_token",
            field=models.UUIDField(db_index=True, default=uuid.uuid4, unique=True),
        ),
        migrations.AddField(
            model_name="partquoterequest",
            name="operator_message",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="partquoterequest",
            name="part_option_id",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="partquoterequest",
            name="prepared_weight_kg",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=8, null=True),
        ),
        migrations.AddField(
            model_name="partquoterequest",
            name="price_ready_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="partquoterequest",
            name="quote_id",
            field=models.CharField(blank=True, max_length=120),
        ),
    ]
