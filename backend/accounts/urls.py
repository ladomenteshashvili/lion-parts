from django.urls import path

from .views import (
    demo_login,
    get_profile,
    login_with_password,
    reset_password,
    send_password_reset_code,
    send_legal_entity_mobile_verification_code,
    send_phone_verification_code,
    set_profile_password,
    upsert_legal_entity_profile,
    verify_legal_entity_mobile_code,
    verify_phone_code,
)

urlpatterns = [
    path("profile/", get_profile, name="accounts-profile"),
    path("profile/password/", set_profile_password, name="accounts-profile-password"),
    path("profile/legal-entity/", upsert_legal_entity_profile, name="accounts-profile-legal-entity"),
    path(
        "profile/legal-entity/send-code/",
        send_legal_entity_mobile_verification_code,
        name="accounts-profile-legal-entity-send-code",
    ),
    path(
        "profile/legal-entity/verify-code/",
        verify_legal_entity_mobile_code,
        name="accounts-profile-legal-entity-verify-code",
    ),
    path("login-password/", login_with_password, name="accounts-login-password"),
    path("password-reset/send-code/", send_password_reset_code, name="accounts-password-reset-send-code"),
    path("password-reset/", reset_password, name="accounts-password-reset"),
    path("demo-login/", demo_login, name="accounts-demo-login"),
    path("send-code/", send_phone_verification_code, name="accounts-send-code"),
    path("verify-code/", verify_phone_code, name="accounts-verify-code"),
]