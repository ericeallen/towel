# A try statement nested in an if binds its handler's name and deletes it as
# the clause ends: nothing is left bound for the block to lose, so the try
# moves on its own. Its handler names differ between the sites and are read
# only inside the handler, where they are always bound.
def first(values, limit):
    if limit > 0:
        try:
            ratio = values[0] / values[1]
            scaled = ratio * limit
        except (ZeroDivisionError, IndexError) as problem:
            print("caught", type(problem).__name__, problem)
        else:
            print("scaled", scaled)
        for value in values:
            print("value", value)
    return limit
def second(values, limit):
    if limit > 0:
        try:
            ratio = values[0] / values[1]
            scaled = ratio * limit
        except (ZeroDivisionError, IndexError) as error:
            print("caught", type(error).__name__, error)
        else:
            print("scaled", scaled)
        while limit > 2:
            limit -= 1
    return -limit
if __name__ == "__main__":
    for function in (first, second):
        for values in ([1, 2], [1, 0], [1]):
            print(function.__name__, values, function(values, 3))
