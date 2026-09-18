"""The exception hierarchy: a base, and one class per condition Towel raises."""

from towel.unification.exceptions import RefactoringError, TowelError, UnsupportedLayoutError


def test_every_towel_error_is_a_towel_error() -> None:
    assert issubclass(RefactoringError, TowelError)
    assert issubclass(UnsupportedLayoutError, TowelError)


def test_an_unsupported_layout_is_still_a_value_error_for_older_callers() -> None:
    assert issubclass(UnsupportedLayoutError, ValueError)
    try:
        raise UnsupportedLayoutError("hatch.toml")
    except ValueError as error:
        assert str(error) == "hatch.toml"
