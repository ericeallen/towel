from pkg.checks import check_total


def test_total():
    items = [1, 5]
    total = sum(item * 2 for item in items)
    assert total < 10, "total too large"
    total += 1
    return total


def test_check():
    check_total([2, 3])
