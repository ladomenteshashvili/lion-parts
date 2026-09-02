from django.urls import path

from .views import (
    checkout,
    demo_confirm_payment,
    demo_request_item_change,
    demo_resolve_item_action,
    demo_update_item_status,
    get_order_detail,
    list_orders,
)

urlpatterns = [
    path("", list_orders, name="orders-list"),
    path("checkout/", checkout, name="orders-checkout"),
    path(
        "<str:order_number>/demo-confirm-payment/",
        demo_confirm_payment,
        name="orders-demo-confirm-payment",
    ),
    path("<str:order_number>/", get_order_detail, name="orders-detail"),
    path(
        "items/<int:item_id>/demo-resolve-action/",
        demo_resolve_item_action,
        name="orders-item-demo-resolve-action",
    ),
    path(
        "items/<int:item_id>/demo-request-change/",
        demo_request_item_change,
        name="orders-item-demo-request-change",
    ),
    path(
        "items/<int:item_id>/demo-update-status/",
        demo_update_item_status,
        name="orders-item-demo-update-status",
    ),
]