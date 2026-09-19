class Token:
    def __init__(self, kind, size):
        self.kind = kind
        self.size = size

    def __repr__(self):
        return f"Token({self.kind!r}, {self.size})"
