from typing import Optional
class Style: pass
class Link: pass
def a1(rows):
    idx = 0
    # captured is a snapshot taken at the first row
    # and stays None until then
    captured: Optional[Style] = None
    for r in rows:
        if captured is None:
            captured = r
        idx += 1
    print("a1", idx, captured)
def a2(rows):
    first = True
    # state tracks the last seen row
    # as a string
    state: Optional[Link] = None
    for r in rows:
        if state is None:
            state = str(r)
        first = False
    print("a2", first, state)
if __name__ == "__main__":
    a1([5, 6])
    a2([7, 8])
