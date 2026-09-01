from django.urls import path
from .views import checkout, list_orders

urlpatterns = [
    path("", list_orders, name="orders-list"),
    path("checkout/", checkout, name="orders-checkout"),
]