"""Checkout (G§7.12, G§8.6)."""

from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from api.errors import ApiError
from cart.models import Cart
from cart.services import cart_data, get_cart, line_status, sync_prices
from payments.services import create_payment
from pricing.currency import display_rate
from pricing.discounts import round2
from pricing.models import PickupPoint, SiteSettings
from pricing.services import meta_pricing

from .models import Order, OrderItem
from .services import assign_order_number, create_item_event, recalculate_order


def payment_options(total, tariff):
    """split_70_30 (default) and full for split tariffs; only full otherwise."""
    full = {"value": "full", "due_now": f"{total:.2f}", "balance": "0.00"}
    if tariff.payment_terms != "split_70_30":
        return [{**full, "default": True}]
    due_now = round2(total * tariff.split_upfront_percent / 100)
    return [{"value": "split_70_30", "due_now": f"{due_now:.2f}", "balance": f"{total - due_now:.2f}", "default": True},
            {**full, "default": False}]


def checkout_options(request, customer):
    cart = cart_data(request, get_cart(request, create=False))  # current rate, current statuses
    tariff = customer.get_tariff()
    company = getattr(customer, "company", None)
    pricing = meta_pricing()
    return {
        "payer": {"name": customer.full_name, "account_type": customer.account_type,
                  "company_name": company.company_official_name if company else None,
                  "phone": customer.phone, "email": customer.user.email},
        "currency": cart["currency"],
        "payment_terms": tariff.payment_terms,
        "payment_options": payment_options(Decimal(cart["summary"]["total"]), tariff),
        "payment_methods": ["card", "invoice"],
        "pickup_points": pricing["pickup_points"],
        "delivery": pricing["delivery"],
        "summary": cart["summary"],
        "checkout_blocked": cart["checkout_blocked"],
        "checkout_blocked_reasons": cart["checkout_blocked_reasons"],
    }


# Create the order (G§7.12, D16)

def _required(message=None):
    return serializers.ErrorDetail(str(message or serializers.Field.default_error_messages["required"]), "required")


class DeliverySerializer(serializers.Serializer):
    city = serializers.CharField(max_length=60)
    district = serializers.CharField(max_length=60)
    address = serializers.CharField(max_length=255)
    comment = serializers.CharField(max_length=2000, required=False, allow_blank=True, default="")


class CheckoutSerializer(serializers.Serializer):
    delivery_method = serializers.ChoiceField(choices=["pickup", "delivery"])
    pickup_point_id = serializers.IntegerField(required=False, allow_null=True)
    delivery = DeliverySerializer(required=False, allow_null=True)
    payment_option = serializers.ChoiceField(choices=["full", "split_70_30"])
    payment_method = serializers.ChoiceField(choices=["card", "invoice"])
    terms_accepted = serializers.BooleanField()

    def validate_terms_accepted(self, value):
        if value is not True:
            raise serializers.ValidationError(_("You must accept the terms to place an order."), code="required")
        return value

    def validate_payment_option(self, value):
        if value == "split_70_30" and self.context["customer"].get_tariff().payment_terms != "split_70_30":
            raise serializers.ValidationError(_("This payment option is not available for your account."),
                                              code="not_allowed")
        return value

    def validate(self, data):
        if data["delivery_method"] == "pickup":
            point = PickupPoint.objects.filter(pk=data.get("pickup_point_id"), is_active=True).first() \
                if data.get("pickup_point_id") else None
            if point is None:
                raise serializers.ValidationError({"pickup_point_id": [serializers.ErrorDetail(
                    str(_("Choose an available pickup point.")), "invalid")]})
            data["pickup_point"], data["delivery"] = point, None
            return data
        delivery = data.get("delivery")
        if not delivery:
            raise serializers.ValidationError({"delivery": {"city": [_required()], "district": [_required()],
                                                            "address": [_required()]}})
        site = SiteSettings.get()
        cities = {c["code"] for c in site.delivery_cities}
        districts = {(d["code"], d["city"]) for d in site.delivery_districts}
        errors = {}
        if delivery["city"] not in cities:
            errors["city"] = [serializers.ErrorDetail(str(_("Choose a city from the list.")), "invalid")]
        elif (delivery["district"], delivery["city"]) not in districts:
            errors["district"] = [serializers.ErrorDetail(str(_("Choose a district of this city.")), "invalid")]
        if errors:
            raise serializers.ValidationError({"delivery": errors})
        data["pickup_point"] = None
        return data


def billing_snapshot(customer):
    company = getattr(customer, "company", None)
    return {"account_type": customer.account_type, "first_name": customer.first_name,
            "last_name": customer.last_name, "personal_id": customer.personal_id,
            "company_official_name": company.company_official_name if company else None,
            "company_identification_code": company.company_identification_code if company else None,
            "legal_address": company.legal_address if company else None,
            "phone": customer.phone, "email": customer.user.email}


def create_order(customer, data):
    """G§7.12 in one transaction: lock the cart, revalidate it at the current rate, freeze currency, rate
    and every price into the order (D16), empty the cart, create the initial payment. Raises cart_empty,
    cart_invalid (+reasons) or rate_unavailable; nothing is created then. -> (order, payment)."""
    with transaction.atomic():
        cart = Cart.objects.select_for_update().filter(customer=customer).first()
        if cart is None or not cart.items.exists():
            raise ApiError("cart_empty", status=409)
        sync_prices(cart)  # allowed currency + current NBG rate on every line (502 without a rate)
        rate = display_rate(cart.currency)
        now = timezone.now()
        lines = list(cart.items.all())
        reasons = [{"item_id": line.pk, "code": status} for line in lines
                   if (status := line_status(line, now)) in ("expired", "unavailable")]
        if reasons:
            raise ApiError("cart_invalid", status=409, reasons=reasons)

        delivery = data.get("delivery") or {}
        order = Order.objects.create(
            customer=customer, currency=cart.currency, exchange_rate=rate, billing_snapshot=billing_snapshot(customer),
            delivery_method=data["delivery_method"], pickup_point=data["pickup_point"],
            delivery_city=delivery.get("city", ""), delivery_district=delivery.get("district", ""),
            delivery_address=delivery.get("address", ""), delivery_comment=delivery.get("comment", ""),
            payment_method=data["payment_method"], payment_option=data["payment_option"], terms_accepted_at=now)
        assign_order_number(order)
        today = timezone.localdate()
        for line in lines:
            item = OrderItem.objects.create(
                order=order, source=line.source, offer_id=line.offer_id, quote_id=line.quote_id,
                part_number=line.part_number, name=line.name, brand=line.brand, condition=line.condition,
                quantity=line.quantity, vin=line.vin, weight_kg=line.weight_kg, weight_source=line.weight_source,
                eta_min_days=line.eta_min_days, eta_max_days=line.eta_max_days,
                expected_arrival_from=today + timedelta(days=line.eta_min_days) if line.eta_min_days is not None else None,
                expected_arrival_to=today + timedelta(days=line.eta_max_days) if line.eta_max_days is not None else None,
                supplier_price_usd=line.supplier_price_usd, shipping_usd_per_kg=line.shipping_usd_per_kg,
                markup_percent=line.markup_percent, vat_percent=line.vat_percent, list_price_usd=line.list_price_usd,
                discount_usd=line.discount_usd, unit_price_usd=line.unit_price_usd, discount_id=line.discount_id,
                list_unit_price=line.list_unit_price, discount_unit_amount=line.discount_unit_amount,
                unit_price=line.unit_price)
            create_item_event(item, "created", text_code="order_item_added", visible=True)
        recalculate_order(order)
        tariff = customer.get_tariff()
        order.amount_due_now = round2(order.total * tariff.split_upfront_percent / 100) \
            if order.payment_option == "split_70_30" else order.total
        order.save(update_fields=["amount_due_now", "updated_at"])
        cart.items.all().delete()
        payment = create_payment(order, order.payment_method)
    return order, payment
