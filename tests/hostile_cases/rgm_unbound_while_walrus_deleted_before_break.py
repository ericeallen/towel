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
# Gemini review of 2026-10-31, case while_walrus_del_renamed_unbound: alpha and
# beta are one binder renamed between the sites. The while test binds it, and
# the body deletes it before a break, so the read after the loop raises
# UnboundLocalError naming the site's own spelling. A shared helper would name
# alpha at both sites; the second site must keep beta. The second pair unbinds
# the name in the loop's else clause instead.
def f1(xs):
    items = iter(xs)
    while (alpha := next(items)) > 0:
        if alpha == 5:
            del alpha
            break
    return alpha + 10


def f2(ys):
    items = iter(ys)
    while (beta := next(items)) > 0:
        if beta == 5:
            del beta
            break
    return beta + 20


def g1(xs):
    items = iter(xs)
    while (gamma := next(items)) > 0:
        print("g", gamma)
    else:
        if gamma == 0:
            del gamma
    return gamma + 30


def g2(ys):
    items = iter(ys)
    while (delta := next(items)) > 0:
        print("g", delta)
    else:
        if delta == 0:
            del delta
    return delta + 40


if __name__ == "__main__":
    import sys

    def _calls(label, function, argument_sets):
        for arguments in argument_sets:
            try:
                outcome = "-> " + repr(function(*arguments))
            except Exception as error:
                outcome = f"raised {type(error).__name__}: {error}"
            print(label, arguments, outcome)

    module = sys.modules[__name__]
    for name in ("f1", "f2", "g1", "g2"):
        _calls(name, getattr(module, name), [([5],), ([3, 0],), ([2, -1],)])
