"""
Validation utilities.
"""

from api import checkout
from api.checkout import __extracted_func_0


def validate_transaction_amount(amount, currency):
    """Validate transaction amount."""
    # Validation logic (DUPLICATE across multiple levels!)
    if amount <= 0:
        return False
    if not currency:
        return False
    if len(currency) != 3:
        return False
    return __extracted_func_0(amount, currency)


def validate_transaction(transaction):
    """Validate a transaction dictionary."""
    amount = transaction.get("amount", 0)
    currency = transaction.get("currency", "")
    return validate_transaction_amount(amount, currency)
