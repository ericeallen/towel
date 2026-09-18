import os
import tempfile
def tf1(data):
    prefix = data[:1]
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt")
    tmp.write(data)
    tmp.flush()
    path = tmp.name
    exists = os.path.exists(path)
    return prefix, exists
def tf2(data):
    suffix = data[-1:]
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt")
    tmp.write(data)
    tmp.flush()
    path = tmp.name
    size = os.path.getsize(path)
    return size, suffix, 2
if __name__ == "__main__":
    print(tf1("abc"), tf2("hello"))
