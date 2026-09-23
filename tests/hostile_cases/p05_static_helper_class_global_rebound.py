# A function rebinds the class's global name after the instance is made.
class C:
    def a(self, items):
        total = 0
        for item in items:
            if item > 4:
                total += item * 2
            else:
                total -= item
        result = total + 7
        return result * 3
    def b(self, items):
        total = 0
        for item in items:
            if item > 5:
                total += item * 2
            else:
                total -= item
        result = total + 7
        return result * 3
inst = C()
def forget():
    global C
    C = None
forget()
if __name__ == "__main__":
    print(inst.a([1, 5, 9]), inst.b([1, 5, 9]))
