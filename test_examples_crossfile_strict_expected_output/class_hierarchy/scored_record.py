"""
Subclass in a second module: its report repeats the base class's summary.
"""

from base_record import Record


class ScoredRecord(Record):
    """A record that also carries a score."""

    def __init__(self, name, values, score):
        super().__init__(name, values)
        self.score = score

    def report(self):
        """The base statistics with the score and its rank against the mean."""
        # Series statistics (DUPLICATED from Record.summary!)
        count = len(self.values)
        total = sum(self.values)
        mean = total / count if count else 0
        spread = max(self.values) - min(self.values) if count else 0
        rank = "above" if self.score > mean else "at or below"
        return {"name": self.name, "count": count, "spread": spread, "score": self.score, "rank": rank}
