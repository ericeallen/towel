"""
Validation utilities.
"""
from api.checkout import validate_checkout_amount


def validate_transaction_amount(amount, currency):
    """Validate transaction amount."""
    # Validation logic (DUPLICATE across multiple levels!)
    return validate_checkout_amount(amount, currency)


def validate_transaction(transaction):
    """Validate a transaction dictionary."""
    amount = transaction.get("amount", 0)
    currency = transaction.get("currency", "")
    return validate_transaction_amount(amount, currency)
