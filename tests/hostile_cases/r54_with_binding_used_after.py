import io
def w1(text):
    with io.StringIO(text) as f:
        data = f.read()
    data = data.upper()
    print("w1", len(data))
    return data, f.closed
def w2(text):
    with io.StringIO(text) as f:
        data = f.read()
    data = data.upper()
    print("w2", len(data))
    return data, f.closed
if __name__ == "__main__":
    print(w1("ab"), w2("cde"))
