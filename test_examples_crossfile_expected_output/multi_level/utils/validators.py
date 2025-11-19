"""
Validation utilities.
"""
from api.checkout import extracted_func


def validate_transaction_amount(amount, currency):
    """Validate transaction amount."""
    # Validation logic (DUPLICATE across multiple levels!)
    return extracted_func(amount, currency)


def validate_transaction(transaction):
    """Validate a transaction dictionary."""
    amount = transaction.get("amount", 0)
    currency = transaction.get("currency", "")
    return validate_transaction_amount(amount, currency)
