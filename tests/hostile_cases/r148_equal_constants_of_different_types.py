# 0, 0.0 and False compare equal and hash alike, as do 1, 1.0 and True, yet
# each prints and behaves as its own type: no helper may hard-code one
# block's literal for another's, and a False elsewhere in a block is not an
# occurrence of its 0.
def score_int(text):
    total = len(text)
    if total > 3:
        return total * 0.5
    return 0
def score_float(text):
    total = len(text)
    if total > 3:
        return total * 0.5
    return 0.0
def flag_true(items):
    count = len(items)
    if count > 3:
        return count + 2
    return True
def flag_one(items):
    count = len(items)
    if count > 3:
        return count + 2
    return 1
def reset_zero(state):
    state["count"] = 0
    state["done"] = False
    print("reset", state)
    return state
def reset_one(state):
    state["count"] = 1
    state["done"] = False
    print("reset", state)
    return state
if __name__ == "__main__":
    print(score_int("ab"), score_float("ab"), score_int("abcd"), score_float("abcd"))
    print(flag_true([]), flag_one([]), flag_true([1, 2, 3, 4]), flag_one([1, 2, 3, 4]))
    print(reset_zero({}), reset_one({}))
