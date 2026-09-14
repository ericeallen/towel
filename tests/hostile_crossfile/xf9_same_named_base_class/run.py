from pkg.one.introspect import IntrospectEndpoint
from pkg.one.revoke import RevocationEndpoint
from pkg.two.access import AccessEndpoint
from pkg.two.request import RequestEndpoint

for cls in (RevocationEndpoint, IntrospectEndpoint, RequestEndpoint, AccessEndpoint):
    endpoint = cls("validator", ("a", "b"))
    print(cls.__name__, endpoint.calls, endpoint.kinds, endpoint.label)
