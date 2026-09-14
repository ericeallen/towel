from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from decimal import Decimal as Money
class Account:
    def open(self, owner):
        self.owner = owner
        self.balance: Money = 0
        self.history: list[Money] = []
        return self
    def reopen(self, owner):
        self.owner = owner
        self.balance: Money = 0
        self.history: list[Money] = []
        return self
if __name__ == "__main__":
    a = Account().open("ann")
    b = Account().reopen("bob")
    print(a.owner, a.balance, a.history, b.owner, b.balance, b.history)
