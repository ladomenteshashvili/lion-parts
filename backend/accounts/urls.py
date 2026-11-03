from django.urls import path

from . import views

urlpatterns = [
    path("auth/register/", views.register, name="auth-register"),
    path("auth/register/resend-code/", views.register_resend_code, name="auth-register-resend-code"),
]
