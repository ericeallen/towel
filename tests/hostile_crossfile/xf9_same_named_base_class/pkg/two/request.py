from .base import BaseEndpoint


class RequestEndpoint(BaseEndpoint):
    def __init__(self, validator, kinds):
        BaseEndpoint.__init__(self)
        self.validator = validator
        self.kinds = list(kinds)
        self.calls.append("request")
        self.label = "request"
