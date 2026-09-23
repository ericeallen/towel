PLUGINS = []


def register(function):
    PLUGINS.append(function.__name__)
    return function
