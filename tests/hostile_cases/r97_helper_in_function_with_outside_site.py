def build(items, result):
    def head(item):
        result.append(("head", item))
        result.append(("sep", 1))
        result.append(("text", item))
    def tail(item):
        result.append(("tail", item))
        result.append(("sep", 1))
        result.append(("text", item))
    for item in items:
        head(item)
        tail(item)
    return result
def plain(item, result):
    result.append(("plain", item))
    result.append(("sep", 1))
    result.append(("text", item))
    return result
if __name__ == "__main__":
    print(build(["a", "b"], []))
    print(plain("c", []))
