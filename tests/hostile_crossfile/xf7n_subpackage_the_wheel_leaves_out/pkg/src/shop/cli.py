# The import of devtools runs only for a developer's command, so it shows nothing ships.
def main(argv):
    if argv and argv[0] == "dev":
        from .devtools.dump import dump

        return dump([1, 2])
    return "run"
