def j1(data):
    import json
    text = json.dumps(data, sort_keys=True)
    size = len(text)
    print("j1", size)
    return json.loads(text), size
def j2(data):
    import json
    text = json.dumps(data, sort_keys=True)
    size = len(text)
    print("j2", size)
    return json.loads(text), size
if __name__ == "__main__":
    print(j1({"a": 1}), j2({"b": [1, 2]}))
