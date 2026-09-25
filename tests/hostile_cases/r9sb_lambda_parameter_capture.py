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

# Round-4 P1-03: a lambda parameter spelled like the free name that becomes
# a helper parameter is the lambda's own; substituting the parameter inside
# the lambda made every key the current row, and max returned the wrong one.
def best_order(order, orders):
    best = max(orders, key=lambda order: order["total"])
    print("best", best["id"], "current", order["id"])
    return best["id"]


def best_quote(quote, quotes):
    best = max(quotes, key=lambda quote: quote["total"])
    print("best", best["id"], "current", quote["id"])
    return best["id"]


def ranked_orders(order, orders):
    ranked = sorted(orders, key=lambda order: -order["total"])
    print("ranked", [row["id"] for row in ranked], "current", order["id"])
    return [row["id"] for row in ranked if row is not order]


def ranked_quotes(quote, quotes):
    ranked = sorted(quotes, key=lambda other: -other["total"])
    print("ranked", [row["id"] for row in ranked], "current", quote["id"])
    return [row["id"] for row in ranked if row is not quote]


if __name__ == "__main__":
    rows = [{"id": "a", "total": 5}, {"id": "b", "total": 9}, {"id": "c", "total": 1}]
    print(best_order(rows[2], rows), best_quote(rows[0], rows))
    print(ranked_orders(rows[1], rows), ranked_quotes(rows[0], rows))
