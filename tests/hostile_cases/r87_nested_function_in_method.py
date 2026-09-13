class Group:
    def __init__(self):
        self.cmds = []
    def add(self, c):
        self.cmds.append(c)
    def command(self, *args, **kwargs):
        def decorator(f):
            cmd = ("command", f, args, kwargs)
            self.add(cmd)
            return cmd
        return decorator
    def group(self, *args, **kwargs):
        def decorator(f):
            cmd = ("group", f, args, kwargs)
            self.add(cmd)
            return cmd
        return decorator
if __name__ == "__main__":
    g = Group()
    print(g.command(1)("f1"))
    print(g.group(2, k=3)("f2"))
    print(g.cmds)
