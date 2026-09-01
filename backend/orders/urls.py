from django.urls import path

from django.urls import path
from .views import (
    checkout,
    demo_request_item_change,
    demo_resolve_item_action,
    get_order_detail,
    list_orders,
)

urlpatterns = [
    path("", list_orders, name="orders-list"),
    path("checkout/", checkout, name="orders-checkout"),
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
]