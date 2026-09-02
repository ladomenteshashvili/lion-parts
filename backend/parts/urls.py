from django.urls import path

from .views import create_quote_request, search_parts

urlpatterns = [
    path("search/", search_parts, name="parts-search"),
    path("quote-requests/", create_quote_request, name="parts-quote-request-create"),
]