from .base import BaseEndpoint


class IntrospectEndpoint(BaseEndpoint):
    def __init__(self, validator, kinds):
        BaseEndpoint.__init__(self)
        self.validator = validator
        self.kinds = list(kinds)
        self.calls.append("introspect")
        self.label = "introspect"
