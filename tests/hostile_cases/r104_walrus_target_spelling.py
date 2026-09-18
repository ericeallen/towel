def first_positive(values):
    if (found := next((v for v in values if v > 0), None)) is not None:
        print("found", found)
        return found * 2
    print("none")
    return None
def first_negative(values):
    if (hit := next((v for v in values if v > 0), None)) is not None:
        print("found", hit)
        return hit * 2
    print("none")
    return None
if __name__ == "__main__":
    print(first_positive([-1, 3, 5]), first_negative([0, 0]), first_negative([7]))
