from django.db import models

# Create your models here.
from django.db import models


class Customer(models.Model):
    session_id = models.CharField(max_length=120, db_index=True)
    name = models.CharField(max_length=120)
    phone = models.CharField(max_length=40, db_index=True)

    is_phone_verified = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return f"{self.name} · {self.phone}"