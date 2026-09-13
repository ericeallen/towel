class Base:
    tokens = {}
class LexerA(Base):
    def rules(ttype):
        return [
            ("close", ttype),
            ("open", ttype, "expr"),
            ("text", ttype),
        ]
    tokens = {"single": rules("String.Single"), "double": rules("String.Double")}
    del rules
class LexerB(Base):
    def rules(ttype):
        return [
            ("close", ttype),
            ("open", ttype, "expr"),
            ("text", ttype),
        ]
    tokens = {"single": rules("Str.S"), "double": rules("Str.D")}
    del rules
if __name__ == "__main__":
    print(LexerA.tokens)
    print(LexerB.tokens)
