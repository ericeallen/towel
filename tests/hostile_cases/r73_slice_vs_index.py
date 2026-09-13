def s1(sequence, start, stop):
    if not sequence:
        raise ValueError("empty")
    window = sequence[start:stop]
    return list(window), len(sequence)
def s2(sequence, start, stop):
    if not sequence:
        raise ValueError("empty")
    window = sequence[start]
    return list(window), len(sequence)
if __name__ == "__main__":
    print(s1([1, 2, 3], 0, 2), s2([[1], [2]], 1, 9))
