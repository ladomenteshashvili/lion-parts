"""Included from config/urls.py only if settings.ENABLE_DEMO_ENDPOINTS. Later epics add their
demo routes here (payments fake-complete, order status/action, courier fee, quote ready)."""

from django.urls import path

from . import demo

urlpatterns = [
    path("outbox/", demo.outbox, name="demo-outbox"),
]
