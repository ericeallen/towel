def leaf_summary(items):
    total = sum(items)
    parts = [str(i) for i in items]
    text = ",".join(parts)
    print("leaf", total)
    return text + "=" + str(total)
