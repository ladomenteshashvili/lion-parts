from django.urls import path
from .views import health_check, meta

urlpatterns = [
    path("health/", health_check, name="health-check"),
    path("meta/", meta, name="meta"),
]
