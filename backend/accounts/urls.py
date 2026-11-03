from django.urls import path

from . import views

urlpatterns = [
    path("auth/csrf/", views.csrf, name="auth-csrf"),
    path("auth/me/", views.me, name="auth-me"),
    path("auth/login/", views.login_view, name="auth-login"),
    path("auth/logout/", views.logout_view, name="auth-logout"),
    path("auth/email/send-verification/", views.email_send_verification, name="auth-email-send-verification"),
    path("auth/email/verify/", views.email_verify, name="auth-email-verify"),
    path("auth/register/", views.register, name="auth-register"),
    path("auth/register/verify-phone/", views.register_verify_phone, name="auth-register-verify-phone"),
    path("auth/register/resend-code/", views.register_resend_code, name="auth-register-resend-code"),
]
