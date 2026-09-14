class Row:
    def __init__(self, items):
        self.items = items
    def children(self):
        def head(item, result):
            result.append(("row-head", item))
            result.append(("sep", 1))
            result.append(("text", item))
        def tail(item, result):
            result.append(("row-tail", item))
            result.append(("sep", 1))
            result.append(("text", item))
        out = []
        for item in self.items:
            head(item, out)
            tail(item, out)
        return out
class Column:
    def __init__(self, items):
        self.items = items
    def children(self):
        def head(item, result):
            result.append(("col-head", item))
            result.append(("sep", 1))
            result.append(("text", item))
        def tail(item, result):
            result.append(("col-tail", item))
            result.append(("sep", 1))
            result.append(("text", item))
        out = []
        for item in self.items:
            head(item, out)
            tail(item, out)
        return out
if __name__ == "__main__":
    print(Row(["a"]).children())
    print(Column(["b"]).children())
