import asyncio


async def tick(v):
    await asyncio.sleep(0)
    return v * 2


async def coro1(x):
    a = x + 1
    b = await tick(a)
    c = b + 10
    return c


async def coro2(x):
    a = x + 1
    b = await tick(a)
    c = b + 20
    return c
