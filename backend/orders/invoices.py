"""Invoice HTML (G§7.10 invoice flow, G§4.11): seller from SiteSettings (translated), buyer from the
order's billing snapshot, the order's frozen currency and amounts. Rendered by a template, so every
value is HTML-escaped."""

from django.conf import settings
from django.template.loader import render_to_string
from django.utils import translation

from api.i18n import tr
from pricing.models import SiteSettings

from .texts import format_money


def invoice_language(requested, fallback):
    codes = {code for code, _name in settings.LANGUAGES}
    return requested if requested in codes else fallback


def render_invoice(order, language):
    with translation.override(language):
        site = SiteSettings.get()
        money = lambda amount: format_money(amount, order.currency)
        names = {d["code"]: tr(d.get("name")) for d in site.delivery_cities + site.delivery_districts}
        context = {
            "language": language, "order": order, "buyer": order.billing_snapshot,
            "seller": {"name": tr(site.invoice_seller_name_i18n), "code": site.invoice_seller_code,
                       "address": tr(site.invoice_seller_address_i18n), "phone": site.invoice_seller_phone,
                       "email": site.invoice_seller_email, "bank_details": tr(site.invoice_bank_details_i18n),
                       "payment_due": tr(site.invoice_payment_due_text_i18n),
                       "footer": tr(site.invoice_footer_text_i18n)},
            "pickup_point": tr(order.pickup_point.name_i18n) if order.pickup_point_id else "",
            "delivery_place": ", ".join(filter(None, (names.get(order.delivery_city, order.delivery_city),
                                                      names.get(order.delivery_district, order.delivery_district)))),
            "lines": [{"item": item, "list": money(item.list_unit_price), "discount": money(item.discount_unit_amount),
                       "unit": money(item.unit_price), "total": money(item.line_total)}
                      for item in order.items.all()],
            "money": {f: money(getattr(order, f)) for f in ("items_total", "discount_total", "courier_fee", "total",
                                                            "amount_paid", "balance_due", "amount_due_now")},
        }
        return render_to_string("orders/invoice.html", context)
