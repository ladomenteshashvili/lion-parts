from django.urls import path
from .views import demo_login, get_profile

urlpatterns = [
    path("profile/", get_profile, name="accounts-profile"),
    path("demo-login/", demo_login, name="accounts-demo-login"),
]