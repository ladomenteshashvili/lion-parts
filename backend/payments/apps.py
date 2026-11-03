from django.apps import AppConfig


class PaymentsConfig(AppConfig):
    name = "payments"

    def ready(self):
        from .providers import get_provider

        get_provider()  # an unknown PAYMENT_PROVIDER stops the startup (ImproperlyConfigured)
