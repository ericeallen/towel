"""
Validation utilities.
"""

from api import checkout


def validate_transaction_amount(amount, currency):
    """Validate transaction amount."""
    # Validation logic (DUPLICATE across multiple levels!)
    if amount <= 0:
        return False
    if not currency:
        return False
    if len(currency) != 3:
        return False
    if currency not in ["USD", "EUR", "GBP"]:
        return False
    if amount > 1000000:
        return False
    return True


def validate_transaction(transaction):
    """Validate a transaction dictionary."""
    amount = transaction.get("amount", 0)
    currency = transaction.get("currency", "")
    return validate_transaction_amount(amount, currency)
