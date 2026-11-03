from django.urls import path

from . import views

urlpatterns = [
    path("parts/search/", views.search, name="parts-search"),
    path("parts/history/", views.history, name="parts-history"),
    path("parts/history/seen/", views.history_seen, name="parts-history-seen"),
    path("parts/quote-requests/", views.quote_requests, name="parts-quote-requests"),
    path("public/q/<uuid:token>/", views.public_quote, name="public-quote"),
]
