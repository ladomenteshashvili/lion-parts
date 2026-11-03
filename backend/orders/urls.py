from django.urls import path

from . import views

urlpatterns = [
    path("checkout/options/", views.checkout_options, name="checkout-options"),
    path("orders/checkout/", views.checkout_create, name="checkout"),
    path("orders/", views.order_list, name="order-list"),
    path("orders/items/<int:item_id>/decision/", views.item_decision, name="order-item-decision"),
    path("orders/<str:order_number>/", views.order_detail, name="order-detail"),
    path("orders/<str:order_number>/payments/", views.order_payments, name="order-payments"),
    path("orders/<str:order_number>/payments/<int:payment_id>/", views.order_payment, name="order-payment"),
    path("orders/<str:order_number>/invoice/", views.invoice, name="order-invoice"),
]
