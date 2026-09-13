class Lexer:
    tokens = {}
class RubyLexer(Lexer):
    def gen_rules():
        states = {}
        for lbrace, rbrace, name in (("{", "}", "cb"), ("[", "]", "sb")):
            states[name + "-string"] = [(lbrace, "push"), (rbrace, "pop")]
            states.setdefault("strings", []).append((lbrace, name + "-string"))
            states[name + "-count"] = len(states)
        return states
    tokens = gen_rules()
    del gen_rules
class CrystalLexer(Lexer):
    def gen_rules():
        states = {}
        for lbrace, rbrace, name in (("(", ")", "pa"), ("<", ">", "ab")):
            states[name + "-string"] = [(lbrace, "push"), (rbrace, "pop")]
            states.setdefault("strings", []).append((lbrace, name + "-string"))
            states[name + "-count"] = len(states)
        return states
    tokens = gen_rules()
    del gen_rules
if __name__ == "__main__":
    print(RubyLexer.tokens)
    print(CrystalLexer.tokens)
