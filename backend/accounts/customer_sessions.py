from .models import Customer, CustomerSession


def get_customer_for_session(session_id):
    session_id = str(session_id or "").strip()

    if not session_id:
        return None

    customer_session = (
        CustomerSession.objects.select_related("customer")
        .filter(session_id=session_id)
        .first()
    )

    if customer_session:
        return customer_session.customer

    customer = Customer.objects.filter(session_id=session_id).first()

    if customer:
        CustomerSession.objects.get_or_create(
            session_id=session_id,
            defaults={"customer": customer},
        )

    return customer


def attach_customer_session(customer, session_id):
    session_id = str(session_id or "").strip()

    if not customer or not session_id:
        return None

    customer_session, _created = CustomerSession.objects.update_or_create(
        session_id=session_id,
        defaults={"customer": customer},
    )

    if customer.session_id != session_id:
        customer.session_id = session_id
        customer.save(update_fields=["session_id", "updated_at"])

    return customer_session
