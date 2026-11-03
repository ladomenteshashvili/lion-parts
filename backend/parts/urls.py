from django.urls import path

from . import views

urlpatterns = [
    path("parts/search/", views.search, name="parts-search"),
]
