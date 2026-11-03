"""Included from config/urls.py only if settings.ENABLE_DEMO_ENDPOINTS. Later epics add their
demo routes here (payments fake-complete, order status/action, courier fee, quote ready)."""

from django.urls import path

from orders import demo as orders_demo

from . import demo

urlpatterns = [
    path("outbox/", demo.outbox, name="demo-outbox"),
    path("orders/<str:order_number>/items/<int:item_id>/status/", orders_demo.item_status, name="demo-item-status"),
    path("orders/<str:order_number>/items/<int:item_id>/action/", orders_demo.item_action, name="demo-item-action"),
    path("orders/<str:order_number>/courier-fee/", orders_demo.courier_fee, name="demo-courier-fee"),
    path("quote-requests/<int:quote_request_id>/ready/", orders_demo.quote_ready, name="demo-quote-ready"),
]
