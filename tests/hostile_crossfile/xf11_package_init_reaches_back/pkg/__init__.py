class Formatter:
    def render(self, items):
        return ",".join(str(i) for i in items)
def summarize(items):
    formatter = Formatter()
    total = sum(items)
    text = formatter.render(items)
    print("summarize", total)
    return text + ":" + str(total)
