log = []


class Alpha:
    def build(self):
        owner = self
        owner.errors = []

        class First:
            def run(self):
                self.name = "a1"
                try:
                    emit(self, "x")
                except Exception as e:
                    owner.errors.append(e)
                    raise

        class Second:
            def run(self):
                self.name = "a2"
                try:
                    emit(self, "y")
                except Exception as e:
                    owner.errors.append(e)
                    raise

        return [First(), Second()]


class Beta:
    def build(self):
        return ["nothing here that matches the template at all", 1, 2, 3]


def emit(handler, body):
    log.append((handler.name, body))


if __name__ == "__main__":
    for handler in Alpha().build():
        handler.run()
    Beta().build()
    print(log)
