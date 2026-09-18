import pkg
from pkg.sub.leaf import leaf_summary
from pkg.vendor.tool import tool_summary
print(pkg.work([1, 2]), leaf_summary([3, 4]), tool_summary([5, 6]))
