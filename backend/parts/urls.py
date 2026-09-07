from django.urls import path

from .views import calculate_part_price, create_quote_request, get_parts_feed, search_parts

urlpatterns = [
    path("search/", search_parts, name="parts-search"),
    path("feed/", get_parts_feed, name="parts-feed"),
    path("calculate-price/", calculate_part_price, name="parts-calculate-price"),
    path("quote-requests/", create_quote_request, name="parts-quote-request-create"),
]