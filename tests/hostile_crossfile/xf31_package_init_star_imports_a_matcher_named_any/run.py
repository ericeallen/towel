import pkg

print(pkg.f1([1, 2], pkg.Config()), pkg.f2([3], pkg.Config()))
matcher = pkg.Any("x")
print(matcher.label, matcher.matches(3))
