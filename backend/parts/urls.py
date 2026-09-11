from django.urls import path

from .views import (
    acknowledge_prepared_quote,
    calculate_part_price,
    create_quote_request,
    get_parts_feed,
    get_prepared_quote,
    search_parts,
)

urlpatterns = [
    path("search/", search_parts, name="parts-search"),
    path("feed/", get_parts_feed, name="parts-feed"),
    path("calculate-price/", calculate_part_price, name="parts-calculate-price"),
    path("quote-requests/", create_quote_request, name="parts-quote-request-create"),
    path(
        "public/quote-requests/<uuid:token>/",
        get_prepared_quote,
        name="parts-prepared-quote-public",
    ),
    path(
        "public/quote-requests/<uuid:token>/acknowledge/",
        acknowledge_prepared_quote,
        name="parts-prepared-quote-acknowledge",
    ),
]
