from pkg.alpha import check_total


def check_scaled(items):
    total = 0
    for item in items:
        total = total + item * 2
    assert total < 10, "total too large"
    return check_total(items) + total
