class Config:
    factor = 2
def fa(items):
    cfg = Config()
    out = []
    for i in items:
        out.append(i * cfg.factor)
    out.append("a")
    return out
