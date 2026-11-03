from django.urls import path

from . import views

urlpatterns = [
    path("cart/items/", views.add_item, name="cart-add-item"),
]
