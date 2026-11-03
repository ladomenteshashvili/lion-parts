from django.urls import path

from . import views

urlpatterns = [
    path("profile/", views.profile, name="profile"),
    path("profile/password/", views.profile_password, name="profile-password"),
    path("profile/email/", views.profile_email, name="profile-email"),
    path("profile/email/confirm/", views.profile_email_confirm, name="profile-email-confirm"),
    path("auth/csrf/", views.csrf, name="auth-csrf"),
    path("auth/me/", views.me, name="auth-me"),
    path("auth/login/", views.login_view, name="auth-login"),
    path("auth/logout/", views.logout_view, name="auth-logout"),
    path("auth/password-reset/", views.password_reset, name="auth-password-reset"),
    path("auth/password-reset/confirm/", views.password_reset_confirm, name="auth-password-reset-confirm"),
    path("auth/email/send-verification/", views.email_send_verification, name="auth-email-send-verification"),
    path("auth/email/verify/", views.email_verify, name="auth-email-verify"),
    path("auth/register/", views.register, name="auth-register"),
    path("auth/register/verify-phone/", views.register_verify_phone, name="auth-register-verify-phone"),
    path("auth/register/resend-code/", views.register_resend_code, name="auth-register-resend-code"),
]
