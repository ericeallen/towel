def render(volatile, autoescape, out):
    if volatile:
        out.append("(escape if ctx else str)(")
    elif autoescape:
        out.append("escape(")
        out.append("#")
    else:
        out.append("str(")
        out.append("#")
    return out
def render2(volatile, autoescape, out):
    if volatile:
        out.append("dyn(")
    elif autoescape:
        out.append("escape(")
        out.append("#")
    else:
        out.append("str(")
        out.append("#")
    return out
if __name__ == "__main__":
    for f in (render, render2):
        for v in (True, False):
            for a in (True, False):
                print(f.__name__, v, a, f(v, a, []))
