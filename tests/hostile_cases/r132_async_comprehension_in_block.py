import asyncio
async def agen(n):
    for i in range(n):
        yield i
async def ac1(n):
    vals = [v * 2 async for v in agen(n)]
    total = sum(vals)
    vals.append(total)
    return vals
async def ac2(n):
    vals = [v * 2 async for v in agen(n)]
    total = sum(vals)
    vals.append(total)
    return vals + [2]
if __name__ == "__main__":
    print(asyncio.run(ac1(3)), asyncio.run(ac2(2)))
