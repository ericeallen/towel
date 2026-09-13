import asyncio
async def a1(items):
    out = []
    for i in items:
        out.append(i + 1)
    out.append("a1")
    await asyncio.sleep(0)
    return out
async def a2(items):
    out = []
    for i in items:
        out.append(i + 1)
    out.append("a2")
    await asyncio.sleep(0)
    return out
if __name__ == "__main__":
    print(asyncio.run(a1([1])), asyncio.run(a2([2])))
