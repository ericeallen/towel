"""
Checkout API.
"""


def validate_checkout_amount(amount, currency):
    """Validate checkout amount."""
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


def checkout(cart_items, amount, currency):
    """Process checkout."""
    if not validate_checkout_amount(amount, currency):
        return {"error": "Invalid amount or currency"}

    return {"success": True, "items": len(cart_items), "total": amount, "currency": currency}
