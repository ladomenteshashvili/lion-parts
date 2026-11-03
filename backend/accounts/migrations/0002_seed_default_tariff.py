from decimal import Decimal

from django.db import migrations


def seed(apps, schema_editor):
    CustomerTariff = apps.get_model("accounts", "CustomerTariff")
    TariffMarkup = apps.get_model("accounts", "TariffMarkup")
    tariff, _ = CustomerTariff.objects.get_or_create(
        name="Standard", defaults={"is_default": True, "payment_terms": "full"},
    )
    for currency in ("GEL", "USD"):
        TariffMarkup.objects.get_or_create(
            tariff=tariff, currency=currency, defaults={"markup_percent": Decimal("20.00")},
        )


class Migration(migrations.Migration):
    dependencies = [("accounts", "0001_initial")]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
