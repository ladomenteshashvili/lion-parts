"""Seed (G§6.2): GEL/USD with VAT 18, the Beliashvili pickup point, Tbilisi and its districts,
SiteSettings from the env values in use before the pricing app (invoice texts: same text in every
language until translated in admin, LP-BE-1306)."""

from decimal import Decimal

from django.conf import settings
from django.db import migrations

TBILISI_DISTRICTS = [
    ("vake", "ვაკე", "Vake", "Ваке"),
    ("saburtalo", "საბურთალო", "Saburtalo", "Сабуртало"),
    ("didube", "დიდუბე", "Didube", "Дидубе"),
    ("isani", "ისანი", "Isani", "Исани"),
    ("gldani", "გლდანი", "Gldani", "Глдани"),
    ("nadzaladevi", "ნაძალადევი", "Nadzaladevi", "Надзаладеви"),
    ("chughureti", "ჩუღურეთი", "Chughureti", "Чугурети"),
    ("krtsanisi", "კრწანისი", "Krtsanisi", "Крцаниси"),
    ("mtatsminda", "მთაწმინდა", "Mtatsminda", "Мтацминда"),
    ("samgori", "სამგორი", "Samgori", "Самгори"),
]


def same_text(value):
    return {"ka": value, "en": value, "ru": value} if value else {}


def seed(apps, schema_editor):
    CurrencySettings = apps.get_model("pricing", "CurrencySettings")
    PickupPoint = apps.get_model("pricing", "PickupPoint")
    SiteSettings = apps.get_model("pricing", "SiteSettings")

    for code in ("GEL", "USD"):
        CurrencySettings.objects.get_or_create(currency=code, defaults={"vat_percent": Decimal("18.00")})

    if not PickupPoint.objects.exists():
        PickupPoint.objects.create(name_i18n={
            "ka": "ბელიაშვილის გაცემის პუნქტი", "en": "Beliashvili pickup point", "ru": "Пункт выдачи Белиашвили",
        })

    env = lambda name, default="": getattr(settings, name, default)  # these settings move here and go away
    SiteSettings.objects.get_or_create(pk=1, defaults={
        "default_shipping_usd_per_kg": Decimal(str(env("DEFAULT_SHIPPING_USD_PER_KG", "8.00"))),
        "delivery_cities": [{"code": "tbilisi", "name": {"ka": "თბილისი", "en": "Tbilisi", "ru": "Тбилиси"}}],
        "delivery_districts": [
            {"code": code, "city": "tbilisi", "name": {"ka": ka, "en": en, "ru": ru}}
            for code, ka, en, ru in TBILISI_DISTRICTS
        ],
        "invoice_seller_name_i18n": same_text(env("INVOICE_SELLER_NAME", "Lion Parts")),
        "invoice_seller_code": env("INVOICE_SELLER_IDENTIFICATION_CODE"),
        "invoice_seller_address_i18n": same_text(env("INVOICE_SELLER_ADDRESS")),
        "invoice_seller_phone": env("INVOICE_SELLER_PHONE"),
        "invoice_seller_email": env("INVOICE_SELLER_EMAIL"),
        "invoice_bank_details_i18n": same_text(env("INVOICE_SELLER_BANK_DETAILS")),
        "invoice_payment_due_text_i18n": same_text(env("INVOICE_PAYMENT_DUE_TEXT")),
        "invoice_footer_text_i18n": same_text(env("INVOICE_FOOTER_TEXT")),
    })


class Migration(migrations.Migration):
    dependencies = [("pricing", "0001_initial")]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
