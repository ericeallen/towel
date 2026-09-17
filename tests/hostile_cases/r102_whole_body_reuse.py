import math
def scale(items, factor):
    total = sum(items)
    scaled = [math.floor(i * factor / total) for i in items]
    print("scaled", scaled)
    return scaled
def rescale(values, k):
    total = sum(values)
    scaled = [math.floor(i * k / total) for i in values]
    print("scaled", scaled)
    return scaled
def summarize(data, weight, label):
    print(label)
    total = sum(data)
    scaled = [math.floor(i * weight / total) for i in data]
    print("scaled", scaled)
    return scaled
class Report:
    def render(self, data, weight):
        total = sum(data)
        scaled = [math.floor(i * weight / total) for i in data]
        print("scaled", scaled)
        return scaled
if __name__ == "__main__":
    print(scale([1, 2, 3], 10), rescale([4, 5], 3), summarize([6, 7], 2, "s"))
    print(Report().render([8, 9], 4))
