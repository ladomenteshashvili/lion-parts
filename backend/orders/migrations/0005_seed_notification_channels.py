"""LP-BE-901: one channel setting per event type, all off (G§6.5 seed)."""

from django.db import migrations

EVENT_TYPES = ("price_ready", "action_required", "courier_fee_proposed", "support_reply", "status_update",
               "payment_update")


def seed(apps, schema_editor):
    Setting = apps.get_model("orders", "NotificationChannelSetting")
    for event_type in EVENT_TYPES:
        Setting.objects.get_or_create(event_type=event_type, defaults={"channel": "none"})


class Migration(migrations.Migration):
    dependencies = [("orders", "0004_notifications_and_support")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
