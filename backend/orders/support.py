"""Support tickets (G§7.16, G§8.9, LP-BE-1001)."""

from datetime import timedelta

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from django.utils import timezone
from django.utils.translation import get_language, gettext_lazy as _
from rest_framework import serializers

from accounts.validators import normalize_phone
from parts.serializers import VinField

from .models import SupportTicket

DUPLICATE_MINUTES = 2


class TicketSerializer(serializers.Serializer):
    context = serializers.ChoiceField(choices=[c for c, _label in SupportTicket.CONTEXTS])
    part_number = serializers.CharField(max_length=120, required=False, allow_blank=True, default="")
    vin = VinField()
    message = serializers.CharField(max_length=2000)
    contact = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")

    def validate(self, data):
        """Guests must leave an email or a Georgian mobile number; customers' contact is ignored."""
        if self.context["customer"] is not None:
            data["contact"] = ""
            return data
        contact = data["contact"].strip()
        if not contact:
            raise serializers.ValidationError({"contact": [serializers.ErrorDetail(
                str(serializers.Field.default_error_messages["required"]), "required")]})
        try:
            if "@" in contact:
                validate_email(contact)
                data["contact"] = contact.lower()
            else:
                data["contact"] = normalize_phone(contact)
        except DjangoValidationError:
            raise serializers.ValidationError({"contact": [serializers.ErrorDetail(
                str(_("Enter a valid email address or Georgian mobile number.")), "invalid")]}) from None
        return data


def create_ticket(data, *, customer, session_key):
    """The same message from the same customer/session within DUPLICATE_MINUTES is returned, not repeated
    (double submits). -> the ticket."""
    owner = {"customer": customer} if customer else {"customer__isnull": True, "session_key": session_key}
    recent = SupportTicket.objects.filter(**owner, context=data["context"], message=data["message"],
                                          created_at__gte=timezone.now() - timedelta(minutes=DUPLICATE_MINUTES)).first()
    if recent:
        return recent
    return SupportTicket.objects.create(customer=customer, session_key="" if customer else session_key,
                                        language=get_language(), **data)


def ticket_data(ticket):
    return {"id": ticket.pk, "context": ticket.context, "part_number": ticket.part_number, "vin": ticket.vin,
            "message": ticket.message, "status": ticket.status, "operator_reply": ticket.operator_reply,
            "replied_at": ticket.replied_at, "created_at": ticket.created_at}
