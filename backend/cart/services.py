"""Cart services (G§7.7): one cart per customer, one per guest session."""

from accounts.services import ensure_session
from parts.services import request_customer

from .models import Cart


def get_cart(request, create=True):
    """The requester's cart: Cart(customer=...) when logged in, else Cart(session_key=...) for this
    browser session. With create=False, None when there is none yet."""
    customer = request_customer(request)
    if customer is not None:
        if create:
            return Cart.objects.get_or_create(customer=customer)[0]
        return Cart.objects.filter(customer=customer).first()
    if not create:
        key = request.session.session_key
        return Cart.objects.filter(session_key=key).first() if key else None
    return Cart.objects.get_or_create(session_key=ensure_session(request))[0]
