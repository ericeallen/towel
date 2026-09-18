# line separator   in a comment
def f1(v, log):
    log.append("f1-pre-a")
    log.append("f1-pre-b")
    acc = [v]
    acc.append(v * 2)
    acc.append(v * 3)
    acc.append(v * 4)
    log.append("f1-post-a")
    log.append("f1-post-b")
    return acc
def f2(v, log):
    log.append("f2-pre-a")
    log.append("f2-pre-b")
    acc = [v]
    acc.append(v * 2)
    acc.append(v * 3)
    acc.append(v * 4)
    log.append("f2-post-a")
    log.append("f2-post-b")
    return acc
if __name__ == "__main__":
    log = []
    print(f1(1, log), f2(2, log), log)
