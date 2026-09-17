import math
def fb(values, k):
    total = sum(values)
    out = [math.floor(i * k / total) for i in values]
    out.append(len(values))
    return out
def gb(data, weight, label):
    print(label)
    total = sum(data)
    out = [math.floor(i * weight / total) for i in data]
    out.append(len(data))
    return out
