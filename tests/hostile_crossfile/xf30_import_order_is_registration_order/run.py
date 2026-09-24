from pkg.registry import PLUGINS
import pkg.app
import pkg.loader

print(PLUGINS)
print(pkg.app.summarize([1, -2, 3]), pkg.app.summarize_again([4]))
print(pkg.loader.load(["a", "b"]), pkg.loader.load_again(["c"]))
