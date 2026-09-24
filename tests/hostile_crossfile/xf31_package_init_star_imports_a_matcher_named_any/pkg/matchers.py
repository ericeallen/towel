class Any:
    """A matcher that accepts any value."""

    def __init__(self, label: str = "any") -> None:
        self.label = label

    def matches(self, value: object) -> bool:
        return True
