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

# Decorators the project defines that are plain wrappers leave the body
# alone: one calls the function with its own arguments after printing, one
# keeps it in a registry and returns it. Both functions still refactor.
import functools

REGISTRY = {}


def logged(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        print("call", fn.__name__)
        return fn(*args, **kwargs)

    return wrapper


def register(fn):
    REGISTRY[fn.__name__] = fn
    return fn


@logged
def a(items):
    total = 0
    for item in items:
        total = total + item * 2
    result = total + 7
    return result * 3


@register
@logged
def b(items):
    total = 0
    for item in items:
        total = total + item * 2
    result = total + 7
    return result * 5


if __name__ == "__main__":
    print(a([1, 5, 9]), b([2, 4]), sorted(REGISTRY), REGISTRY["b"]([1]))
