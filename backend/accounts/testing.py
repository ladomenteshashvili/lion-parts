"""Test factories shared by the test modules (not used at runtime)."""

from django.contrib.auth import get_user_model
from django.utils import timezone

from .models import Customer

PASSWORD = "S3cure!pass"


def make_customer(email="nino@example.com", phone="555123456", password=PASSWORD, **fields):
    user = get_user_model().objects.create_user(username=email, email=email, password=password)
    defaults = {"account_type": Customer.TYPE_PHYSICAL, "first_name": "Nino", "last_name": "Beridze",
                "phone": phone, "terms_accepted_at": timezone.now()}
    return Customer.objects.create(user=user, **{**defaults, **fields})
