# The class of a class pattern and the root of a dotted value pattern are
# names a parameter can take: `case __param_0():` is still a class pattern
# and `case __param_0.RED:` still a value pattern, as long as the argument is
# passed by value.
import enum
class Light(enum.Enum):
    RED = 1
    GREEN = 2
class Paint(enum.Enum):
    RED = 1
    BLUE = 3
def is_first(value, first, second):
    print("kind", 1)
    match value:
        case first():
            found = "first"
        case _:
            found = "neither"
    return found
def is_second(value, first, second):
    print("kind", 2)
    match value:
        case second():
            found = "second"
        case _:
            found = "neither"
    return found
def red_light(value, lights, paints):
    print("red", 1)
    match value:
        case lights.RED:
            found = "red"
        case _:
            found = "other"
    return found
def red_paint(value, lights, paints):
    print("red", 2)
    match value:
        case paints.RED:
            found = "red"
        case _:
            found = "other"
    return found
if __name__ == "__main__":
    print(is_first(1, int, str), is_first("s", int, str), is_second(1, int, str), is_second("s", int, str))
    print(red_light(Light.RED, Light, Paint), red_light(Paint.RED, Light, Paint))
    print(red_paint(Light.RED, Light, Paint), red_paint(Paint.RED, Light, Paint))
