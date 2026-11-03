from django.urls import path

from . import views

urlpatterns = [
    path("cart/", views.cart, name="cart"),
    path("cart/items/", views.add_item, name="cart-add-item"),
    path("cart/items/<int:item_id>/", views.item, name="cart-item"),
    path("cart/items/<int:item_id>/refresh/", views.refresh_item, name="cart-item-refresh"),
    path("cart/apply-vin/", views.apply_vin, name="cart-apply-vin"),
]
