class Matcher:
	def __init__(self, patterns):
		self.patterns = patterns

	def match_a(self, file):
		include = None
		for index, pattern in enumerate(self.patterns):
			if pattern in file:
				include = index
		return include, "a"

	def match_b(self, file):
		include = None
		for index, pattern in enumerate(self.patterns):
			if pattern in file:
				include = index
		return include, "b"

if __name__ == "__main__":
	m = Matcher(["x", "y"]); print(m.match_a("xy"), m.match_b("zz"))
