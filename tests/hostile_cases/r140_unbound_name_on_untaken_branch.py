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

# A name nothing binds, read only on a branch no run takes. Hoisting it into
# an eager argument raises NameError at every call; the thunk keeps the read
# where the block had it.
def f(*args):
    return sum(args)
def site_one(src, limit, alpha):
    if limit < 0:
        v = zeta
    v = alpha
    v = 0
    f(src)
    return v + 1
def site_two(src, limit, beta):
    if limit < 0:
        v = zeta
    v = beta
    v = 0
    f(src)
    return v - 1
if __name__ == "__main__":
    print(site_one(1, 2, 3), site_two(4, 5, 6))
