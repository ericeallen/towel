def check_total(items):
    total = sum(item * 2 for item in items)
    assert total < 10, "total too large"
    total += 1
    return total
