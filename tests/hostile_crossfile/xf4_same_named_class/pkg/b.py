class Config:
    factor = 5
def fb(items):
    cfg = Config()
    out = []
    for i in items:
        out.append(i * cfg.factor)
    out.append("b")
    return out
