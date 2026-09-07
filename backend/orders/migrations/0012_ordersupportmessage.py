# Generated manually for order support messaging

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0011_orderitem_alternative_fields"),
    ]

    operations = [
        migrations.CreateModel(
            name="OrderSupportMessage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("sender_type", models.CharField(choices=[("customer", "Customer"), ("operator", "Operator"), ("system", "System")], db_index=True, default="operator", max_length=30)),
                ("sender_name", models.CharField(blank=True, max_length=120)),
                ("message", models.TextField()),
                ("visible_to_customer", models.BooleanField(default=True)),
                ("is_read_by_customer", models.BooleanField(default=False)),
                ("is_read_by_operator", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("item", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="support_messages", to="orders.orderitem")),
                ("order", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="support_messages", to="orders.order")),
            ],
            options={
                "ordering": ["created_at"],
            },
        ),
    ]
