def first(records):
    import json
    payload = {"records": records}
    payload["count"] = len(records)
    return json.dumps(payload)
def second(records):
    import json
    payload = {"records": records}
    payload["count"] = len(records)
    return json.dumps(payload, sort_keys=True)
if __name__ == "__main__":
    print(first([1, 2]), second([3, 4]))
