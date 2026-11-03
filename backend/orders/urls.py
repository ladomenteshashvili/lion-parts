from django.urls import path

from . import views

urlpatterns = [
    path("checkout/options/", views.checkout_options, name="checkout-options"),
    path("orders/checkout/", views.checkout_create, name="checkout"),
    path("orders/<str:order_number>/invoice/", views.invoice, name="order-invoice"),
]
