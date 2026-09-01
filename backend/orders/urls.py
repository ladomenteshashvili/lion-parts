from django.urls import path
from .views import checkout, get_order_detail, list_orders

urlpatterns = [
    path("", list_orders, name="orders-list"),
    path("checkout/", checkout, name="orders-checkout"),
    path("<str:order_number>/", get_order_detail, name="orders-detail"),
]