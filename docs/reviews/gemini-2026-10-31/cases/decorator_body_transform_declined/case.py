def inspect_code_names(fn):
    fn._local_names = fn.__code__.co_varnames
    return fn


@inspect_code_names
def f1(x):
    alpha = x + 1
    beta = alpha * 2
    gamma = beta + 10
    return gamma


@inspect_code_names
def f2(x):
    alpha = x + 1
    beta = alpha * 2
    gamma = beta + 20
    return gamma
