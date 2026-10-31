"""
Base of a class hierarchy whose subclass lives in another module.
"""


class Record:
    """A named series of numbers."""

    def __init__(self, name, values):
        self.name = name
        self.values = list(values)

    def summary(self):
        """Count, total, mean and spread of the series."""
        # Series statistics (DUPLICATED in the subclass's report method!)
        count = len(self.values)
        total = sum(self.values)
        mean = total / count if count else 0
        spread = max(self.values) - min(self.values) if count else 0
        return {"name": self.name, "count": count, "total": total, "mean": mean, "spread": spread}
