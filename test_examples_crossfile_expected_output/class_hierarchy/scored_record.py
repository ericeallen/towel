"""
Subclass in a second module: its report repeats the base class's summary.
"""

from base_record import Record
from base_record import _extracted_func_0


class ScoredRecord(Record):
    """A record that also carries a score."""

    def __init__(self, name, values, score):
        super().__init__(name, values)
        self.score = score

    def report(self):
        """The base statistics with the score and its rank against the mean."""
        # Series statistics (DUPLICATED from Record.summary!)
        count, mean, spread, total = _extracted_func_0(self)
        rank = "above" if self.score > mean else "at or below"
        return {"name": self.name, "count": count, "spread": spread, "score": self.score, "rank": rank}
