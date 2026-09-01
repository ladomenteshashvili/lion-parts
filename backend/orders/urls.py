from django.urls import path
from .views import checkout, get_order_detail, list_orders, demo_resolve_item_action

urlpatterns = [
    path("", list_orders, name="orders-list"),
    path("checkout/", checkout, name="orders-checkout"),
    path("<str:order_number>/", get_order_detail, name="orders-detail"),
    path(
        "items/<int:item_id>/demo-resolve-action/",
        demo_resolve_item_action,
        name="orders-item-demo-resolve-action",
    ),
]