# Copyright 2025-2026 Eric Allen
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Round-4 P1-03 across modules: the lambda's own ``order`` is not the
# helper parameter that the function's ``order`` becomes.
def best_order(order, orders):
    best = max(orders, key=lambda order: order["total"])
    print("best", best["id"], "current", order["id"])
    return best["id"]
