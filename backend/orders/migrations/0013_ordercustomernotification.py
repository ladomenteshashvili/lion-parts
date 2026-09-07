# Generated manually for customer notification magic links

import uuid

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0012_ordersupportmessage"),
    ]

    operations = [
        migrations.CreateModel(
            name="OrderCustomerNotification",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("token", models.UUIDField(db_index=True, default=uuid.uuid4, unique=True)),
                ("notification_type", models.CharField(choices=[("support_reply", "Support reply"), ("order_update", "Order update"), ("action_required", "Action required"), ("notice", "Notice")], db_index=True, default="notice", max_length=40)),
                ("title", models.CharField(max_length=255)),
                ("message", models.TextField(blank=True)),
                ("visible_to_customer", models.BooleanField(default=True)),
                ("is_read_by_customer", models.BooleanField(default=False)),
                ("acknowledged_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("event", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="customer_notifications", to="orders.orderitemevent")),
                ("item", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="customer_notifications", to="orders.orderitem")),
                ("order", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="customer_notifications", to="orders.order")),
                ("support_message", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="customer_notifications", to="orders.ordersupportmessage")),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
    ]
