from django.urls import path
from .views import get_cart, add_cart_item, remove_cart_item

urlpatterns = [
    path("", get_cart, name="cart-detail"),
    path("items/", add_cart_item, name="cart-item-add"),
    path("items/<path:cart_item_id>/", remove_cart_item, name="cart-item-remove"),
]