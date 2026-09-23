"""
Validation utilities.
"""

from api import checkout
from api.checkout import __extracted_func_1


def validate_transaction_amount(amount, currency):
    """Validate transaction amount."""
    # Validation logic (DUPLICATE across multiple levels!)
    return __extracted_func_1(amount, currency)


def validate_transaction(transaction):
    """Validate a transaction dictionary."""
    amount = transaction.get("amount", 0)
    currency = transaction.get("currency", "")
    return validate_transaction_amount(amount, currency)
