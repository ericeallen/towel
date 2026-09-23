"""
Payment service.
"""

from api import checkout
from api.checkout import validate_checkout_amount


def validate_payment_amount(amount, currency):
    """Validate payment amount."""
    # Validation logic (DUPLICATE across multiple levels!)
    return validate_checkout_amount(amount, currency)


def process_payment(user_id, amount, currency):
    """Process a payment."""
    if not validate_payment_amount(amount, currency):
        return {"status": "error", "message": "Invalid payment"}

    return {"status": "success", "user_id": user_id, "amount": amount, "currency": currency}
