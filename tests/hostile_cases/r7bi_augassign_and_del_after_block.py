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

# The block binds count; the only later use is count += 1 or del count, on a
# path that does not always run. Both need the binding: the helper must
# return count, or the later statement raises UnboundLocalError.
def summarize_orders(orders, extra):
    count = len(orders)
    total = sum(orders)
    print("summary", count, total)
    for item in extra:
        print("extra", item)
        count += 1
    return total
def summarize_refunds(refunds, extra):
    count = len(refunds)
    total = sum(refunds)
    print("summary", count, total)
    if extra:
        print("with extras")
        count += len(extra)
    return -total
def drop_one(xs, flag):
    count = len(xs)
    total = sum(xs)
    print("summary", count, total)
    if flag:
        print("dropping")
        del count
    return total
def drop_two(xs, flag):
    count = len(xs)
    total = sum(xs)
    print("summary", count, total)
    for _ in range(flag):
        print("dropping")
        del count
    return -total
if __name__ == "__main__":
    for function, arguments in (
        (summarize_orders, (([1, 2], []), ([1, 2], [9]))),
        (summarize_refunds, (([1, 2], []), ([1, 2], [9]))),
        (drop_one, (([1, 2], 0), ([1, 2], 1))),
        (drop_two, (([1, 2], 0), ([1, 2], 1))),
    ):
        for argument in arguments:
            try:
                print(function.__name__, argument, "->", function(*argument))
            except Exception as error:
                print(function.__name__, argument, "raised", type(error).__name__, error)
