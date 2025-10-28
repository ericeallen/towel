"""
Payment service.
"""
from api.checkout import __extracted_func_344


def validate_payment_amount(amount, currency):
    """Validate payment amount."""
    # Validation logic (DUPLICATE across multiple levels!)
    if amount <= 0:
        return False
    if not currency:
        return False
    if len(currency) != 3:
        return False
    return __extracted_func_344(amount, currency)


def process_payment(user_id, amount, currency):
    """Process a payment."""
    if not validate_payment_amount(amount, currency):
        return {'status': 'error', 'message': 'Invalid payment'}

    return {
        'status': 'success',
        'user_id': user_id,
        'amount': amount,
        'currency': currency
    }
