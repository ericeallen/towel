def __extracted_func_0(item):
    match item:
        case [first, *rest]:
            head = first + 1
            tail_len = len(rest)
        case _:
            head = 0
            tail_len = 0
    return (head, tail_len)


def f1(item):
    head, tail_len = __extracted_func_0(item)
    return head + tail_len + 10


def f2(item):
    head, tail_len = __extracted_func_0(item)
    return (head * 2) + tail_len
