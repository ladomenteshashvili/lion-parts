from django.urls import path
from .views import search_parts

urlpatterns = [
    path("search/", search_parts, name="parts-search"),
]