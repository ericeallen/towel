from . import Formatter
def describe(items):
    formatter = Formatter()
    total = sum(items)
    text = formatter.render(items)
    print("describe", total)
    return text + "=" + str(total)
