from pkg.servers import Large, Small

if __name__ == "__main__":
    for item in (Small([1]), Large([2, 3])):
        print(item.count, item.limit, item.label, item.kind)
