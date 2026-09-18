def tool_summary(items):
    total = sum(items)
    parts = [str(i) for i in items]
    text = ",".join(parts)
    print("tool", total)
    return text + ":" + str(total)
