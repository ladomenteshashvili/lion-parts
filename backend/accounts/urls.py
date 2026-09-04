from django.urls import path

from .views import (
    demo_login,
    get_profile,
    send_phone_verification_code,
    verify_phone_code,
)

urlpatterns = [
    path("profile/", get_profile, name="accounts-profile"),
    path("demo-login/", demo_login, name="accounts-demo-login"),
    path("send-code/", send_phone_verification_code, name="accounts-send-code"),
    path("verify-code/", verify_phone_code, name="accounts-verify-code"),
]