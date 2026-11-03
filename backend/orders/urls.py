from django.urls import path

from . import views

urlpatterns = [
    path("checkout/options/", views.checkout_options, name="checkout-options"),
    path("orders/<str:order_number>/invoice/", views.invoice, name="order-invoice"),
]
