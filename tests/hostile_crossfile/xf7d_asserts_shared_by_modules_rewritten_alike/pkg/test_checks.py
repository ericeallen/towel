from pkg.alpha import check_total
from pkg.beta import check_scaled


def test_total():
    check_total([1, 5])


def test_scaled():
    check_scaled([2, 4])
