"""LP-BE-1305: SMS/email templates for every event x ka/en/ru (G§7.11, owner decision D24: event text,
order/part number and the link only)."""

from django.db import migrations

ORDER = "{{ order.order_number }}"
PART = "{{ notification.quote_request.part_number }}"
TEXTS = {
    "price_ready": {
        "ka": f"Lion Parts: ფასი მზადაა ნაწილზე {PART}.",
        "en": f"Lion Parts: the price for part {PART} is ready.",
        "ru": f"Lion Parts: цена на деталь {PART} готова.",
    },
    "action_required": {
        "ka": f"Lion Parts: შეკვეთა {ORDER} საჭიროებს თქვენს დადასტურებას.",
        "en": f"Lion Parts: order {ORDER} needs your confirmation.",
        "ru": f"Lion Parts: заказ {ORDER} требует вашего подтверждения.",
    },
    "courier_fee_proposed": {
        "ka": f"Lion Parts: დაადასტურეთ მიტანის ღირებულება შეკვეთისთვის {ORDER}.",
        "en": f"Lion Parts: please confirm the delivery fee for order {ORDER}.",
        "ru": f"Lion Parts: подтвердите стоимость доставки заказа {ORDER}.",
    },
    "support_reply": {
        "ka": f"Lion Parts: მხარდაჭერის გუნდმა გიპასუხათ{{% if order %}} შეკვეთაზე {ORDER}{{% endif %}}.",
        "en": f"Lion Parts: you have a new reply from our support team{{% if order %}} about order {ORDER}{{% endif %}}.",
        "ru": f"Lion Parts: новый ответ службы поддержки{{% if order %}} по заказу {ORDER}{{% endif %}}.",
    },
    "status_update": {
        "ka": f"Lion Parts: შეკვეთის {ORDER} სტატუსი განახლდა.",
        "en": f"Lion Parts: the status of order {ORDER} was updated.",
        "ru": f"Lion Parts: статус заказа {ORDER} обновлён.",
    },
    "payment_update": {
        "ka": f"Lion Parts: შეკვეთის {ORDER} გადახდის სტატუსი განახლდა.",
        "en": f"Lion Parts: the payment status of order {ORDER} was updated.",
        "ru": f"Lion Parts: статус оплаты заказа {ORDER} обновлён.",
    },
}


def seed(apps, schema_editor):
    Template = apps.get_model("orders", "NotificationTemplate")
    for event_type, languages in TEXTS.items():
        for language, text in languages.items():
            Template.objects.get_or_create(event_type_id=event_type, language=language, defaults={
                "sms_template": f"{text} {{{{ link }}}}", "email_subject": "Lion Parts: {{ title }}",
                "email_template": f"{{{{ title }}}}\n\n{text}\n\n{{{{ link }}}}\n"})


class Migration(migrations.Migration):
    dependencies = [("orders", "0006_notification_template")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
