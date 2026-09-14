class Matcher:
    def __init__(self, test):
        self.test = test
    def compare_graph(self, num1, num2):
        if self.test == "graph":
            if num1 != num2:
                return False
        elif not num1 >= num2:
            return False
        if num1 < 0:
            return False
        return True
    def compare_edges(self, num1, num2):
        if self.test == "graph":
            if num1 != num2:
                return False
        elif not num1 >= num2:
            return False
        if num2 < 0:
            return False
        return True
if __name__ == "__main__":
    for test in ("graph", "mono"):
        m = Matcher(test)
        print(test, m.compare_graph(2, 2), m.compare_graph(3, 2), m.compare_edges(2, 2), m.compare_edges(-1, -2))
