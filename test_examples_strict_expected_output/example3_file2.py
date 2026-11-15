"""
Example 3, File 2: Repeated code across multiple files.
"""


def calculate_discount_for_premium_customer(price, customer):
    """Calculate discount for premium customer."""
    # Calculate discount (DUPLICATE across files!)
    base_discount = 0.1
    if customer.get("years_member", 0) > 5:
        base_discount += 0.05
    if customer.get("total_purchases", 0) > 1000:
        base_discount += 0.05

    discount_amount = price * base_discount
    final_price = price - discount_amount

    return final_price


def process_premium_order(order, customer):
    """Process a premium order."""
    total = order.get("total", 0)
    final = calculate_discount_for_premium_customer(total, customer)
    print(f"Premium order: ${final:.2f}")
    return final
