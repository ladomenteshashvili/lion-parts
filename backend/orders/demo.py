"""Demo operator endpoints (G§8.10, LP-BE-807). Mounted only with ENABLE_DEMO_ENDPOINTS (refused in
production). Staff session plus the same model permission as the matching admin action; each calls the
same service as the admin, so money, events and status logic are exercised. The item status endpoint is
the only place that bypasses the balance guard (G§8.10)."""

from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import BasePermission, IsAdminUser
from rest_framework.response import Response

from parts.models import PartQuoteRequest
from parts.services import mark_quote_ready, quote_request_data

from . import services
from .models import Order, OrderItem
from .serializers import order_data, order_queryset


def staff_with(permission):
    class Allowed(BasePermission):
        def has_permission(self, request, view):
            return IsAdminUser().has_permission(request, view) and request.user.has_perm(permission)
    return Allowed


def _apply(run):
    try:
        return run()
    except services.OperatorError as error:
        raise serializers.ValidationError({error.field: [serializers.ErrorDetail(str(error.message), "invalid")]})


def _item(order_number, item_id):
    return get_object_or_404(OrderItem, pk=item_id, order__order_number=order_number)


def _order(request, order_number):
    return Response(order_data(request, order_queryset(detail=True).get(order_number=order_number), detail=True))


class StatusSerializer(serializers.Serializer):
    item_status = serializers.ChoiceField(choices=services.OPERATOR_STATUSES)
    reason = serializers.CharField(required=False, allow_blank=True, max_length=1000, default="")


class ActionSerializer(serializers.Serializer):
    action_type = serializers.ChoiceField(choices=services.ACTION_TYPES)
    message = serializers.CharField(required=False, allow_blank=True, default="")
    proposed_unit_price = serializers.DecimalField(max_digits=12, decimal_places=2, required=False)
    proposed_eta_min_days = serializers.IntegerField(min_value=1, required=False)
    proposed_eta_max_days = serializers.IntegerField(min_value=1, required=False)
    proposed_part_number = serializers.CharField(required=False, allow_blank=True, max_length=120, default="")
    proposed_name = serializers.CharField(required=False, allow_blank=True, max_length=255, default="")
    proposed_weight_kg = serializers.DecimalField(max_digits=8, decimal_places=2, required=False)


class CourierFeeSerializer(serializers.Serializer):
    proposed_fee_gel = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0)
    message = serializers.CharField(required=False, allow_blank=True, default="")


class QuoteReadySerializer(serializers.Serializer):
    weight_kg = serializers.DecimalField(max_digits=8, decimal_places=2)
    operator_message = serializers.CharField(required=False, allow_blank=True, default="")


def _valid(serializer_class, request):
    serializer = serializer_class(data=request.data)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


@api_view(["POST"])
@permission_classes([staff_with("orders.change_orderitem")])
def item_status(request, order_number, item_id):
    data = _valid(StatusSerializer, request)
    item = _item(order_number, item_id)
    _apply(lambda: services.set_item_status(item.pk, data["item_status"], actor_name=request.user.get_username(),
                                            reason=data["reason"], bypass_balance_guard=True))
    return _order(request, order_number)


@api_view(["POST"])
@permission_classes([staff_with("orders.change_orderitem")])
def item_action(request, order_number, item_id):
    data = _valid(ActionSerializer, request)
    item = _item(order_number, item_id)
    _apply(lambda: services.request_item_action(item.pk, actor_name=request.user.get_username(), **data))
    return _order(request, order_number)


@api_view(["POST"])
@permission_classes([staff_with("orders.change_order")])
def courier_fee(request, order_number):
    data = _valid(CourierFeeSerializer, request)
    order = get_object_or_404(Order, order_number=order_number)
    _apply(lambda: services.propose_courier_fee(order.pk, data["proposed_fee_gel"], data["message"],
                                                actor=request.user))
    return _order(request, order_number)


@api_view(["POST"])
@permission_classes([staff_with("parts.change_partquoterequest")])
def quote_ready(request, quote_request_id):
    data = _valid(QuoteReadySerializer, request)
    qr = get_object_or_404(PartQuoteRequest, pk=quote_request_id)
    return Response(quote_request_data(_apply(lambda: mark_quote_ready(qr.pk, data["weight_kg"],
                                                                       data["operator_message"]))))
