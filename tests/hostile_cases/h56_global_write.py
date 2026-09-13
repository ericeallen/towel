state = 0
def g1(v):
    global state
    state = v
    state += 1
    print("g1", state)
    return state
def g2(v):
    global state
    state = v * 2
    state += 1
    print("g2", state)
    return state
if __name__ == "__main__":
    print(g1(1), g2(2), state)
