from django.urls import path

from . import views

urlpatterns = [
    path("payments/<str:provider>/callback/", views.callback, name="payment-callback"),
]
