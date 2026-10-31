# Independent Correctness Review of `towel dry` (Gemini, 2026-10-31)

## Summary

We evaluated `towel dry` (Towel 1.772, Python 3.12.2; the review first said 1.947, but it read a checkout still at 1.772, as the [verification](#verification-claude-2026-10-31) below records) across **28 runnable test programs** in `docs/reviews/gemini-2026-10-31/cases/<name>/`, spanning all 16 requested hazard categories (scopes and bindings, evaluation order, exceptions, aliasing, `return`/`break`/`continue`, generators, `async`, `global`/`nonlocal`, closures, defaults, decorators, comprehensions, walrus `:=`, `match`, and class bodies) and excluding all items listed in `docs/KNOWN_LIMITATIONS.md` and `docs/ADVERSARIAL_REVIEW.md`.
**9 semantic defects were confirmed** where `towel dry` silently changes runtime behavior (wrong return value or raised exception) as demonstrated by `check_<name>.py`; **19 hazards were handled correctly** (either safely extracted with thunks/guards or conservatively declined).
To fix the 9 confirmed defects, update `Scope.lookup` to skip `ast.ClassDef` scopes, teach `walk_own_scope` / `_walk_own_scope_outside` to visit default arguments/decorators/class bases, remove the name-based `_suppresses` assumption on `with` blocks, check trailing semicolon statements and disambiguate same-named methods by line number in `_find_return_variables` / `_leaves_orphans`, respect `global`/`nonlocal` stores in `inline_leading_thunks`, and subtract body deletions after `ast.While` condition evaluation in `_UnboundReads`.

| # | Case Name | Hazard Category | `towel dry` Action | Status |
|---|---|---|---|---|
| 1 | `same_named_methods_semicolon_orphan` | Scopes & bindings / Class bodies | Extracted (`_extracted_func_0`) | **CONFIRMED DEFECT** (`UnboundLocalError`) |
| 2 | `default_arg_frame_inspection` | Defaults / Scopes & bindings | Extracted (`__extracted_func_0`) | **CONFIRMED DEFECT** (missing locals in `dir()`) |
| 3 | `inlined_comp_in_default_thunk_cell` | Comprehensions (PEP 709) / Closures / Defaults | Extracted (`__extracted_func_0` with thunk) | **CONFIRMED DEFECT** (`NameError` on emptied cell) |
| 4 | `aliased_suppress_return_coverage` | Exceptions / `return` coverage | Extracted (`return __extracted_func_0(...)`) | **CONFIRMED DEFECT** (`None` instead of fallthrough) |
| 5 | `aliased_suppress_eager_hoist` | Exceptions / Evaluation order | Extracted (`__extracted_func_0` eager arg) | **CONFIRMED DEFECT** (`UnboundLocalError`) |
| 6 | `class_attr_shadows_global_lookup` | Class bodies / `global` / Evaluation order | Extracted (`_extracted_func_0` eager arg) | **CONFIRMED DEFECT** (stale global value) |
| 7 | `nonlocal_in_class_method_hazard` | `nonlocal` / Class bodies / Closures | Extracted (`__extracted_func_0` eager arg) | **CONFIRMED DEFECT** (stale `nonlocal` cell value) |
| 8 | `thunk_inlined_across_global_store` | Evaluation order / `global` / Thunk inlining | Extracted (`__extracted_func_0` inlined thunk) | **CONFIRMED DEFECT** (pre-store global read) |
| 9 | `while_walrus_del_renamed_unbound` | Walrus (`:=`) / `break` / Exceptions | Extracted (`__extracted_func_0` alpha-renamed) | **CONFIRMED DEFECT** (changed `UnboundLocalError` name) |
| 10 | `aliasing_list_mutation_order` | Aliasing / Evaluation order | Extracted (`__extracted_func_0` with thunk) | Handled correctly |
| 11 | `async_await_not_extracted` | `async` / Evaluation order | Declined extraction | Handled correctly |
| 12 | `class_body_zero_arg_super` | Class bodies / Closures (`__class__` cell) | Declined extraction | Handled correctly |
| 13 | `class_private_name_mangling` | Class bodies / Scopes & bindings (`__secret`) | Declined extraction | Handled correctly |
| 14 | `closure_mutated_cell_boundary` | Closures / Scopes & bindings | Declined extraction | Handled correctly |
| 15 | `comprehension_walrus_leaks_local` | Comprehensions / Walrus (`:=`) | Declined extraction | Handled correctly |
| 16 | `decorator_body_transform_declined` | Decorators / Scopes & bindings | Declined extraction | Handled correctly |
| 17 | `early_return_partial_coverage` | `return` / Control flow | Declined extraction | Handled correctly |
| 18 | `eval_order_impure_call_thunked` | Evaluation order | Declined extraction | Handled correctly |
| 19 | `except_handler_target_deletion` | Exceptions / Scopes & bindings | Declined extraction | Handled correctly |
| 20 | `generator_yield_not_extracted` | Generators (`yield`) | Declined extraction | Handled correctly |
| 21 | `global_nonlocal_declaration_shared` | `global` / `nonlocal` | Declined extraction | Handled correctly |
| 22 | `inlined_comprehension_top_level_thunk` | Comprehensions (PEP 709) / Closures | Declined extraction | Handled correctly |
| 23 | `loop_break_continue_external` | `break` / `continue` | Declined extraction | Handled correctly |
| 24 | `match_capture_binding_liveness` | `match` / Scopes & bindings | Extracted (`__extracted_func_0`) | Handled correctly |
| 25 | `mutable_default_allocation_per_iteration` | Defaults / Aliasing | Declined extraction | Handled correctly |
| 26 | `possibly_unbound_local_in_thunk` | Scopes & bindings / Exceptions | Declined extraction | Handled correctly |
| 27 | `walrus_in_condition_short_circuit` | Walrus (`:=`) / Evaluation order | Declined extraction | Handled correctly |
| 28 | `weakref_raii_lifetime_escaped` | Scopes & bindings / Object lifetime | Extracted (`__extracted_func_0` returning `box`) | Handled correctly |

---

## Confirmed Semantic Defects

### 1. `same_named_methods_semicolon_orphan`

- **Hazard**: Scopes and bindings / Class bodies / Return variable analysis and orphan detection.
- **Likely `src/towel` functions**:
  - `BlockAnalysis._find_return_variables` (`src/towel/unification/block_analysis.py:640-677`, specifically line 669: `if stmt.lineno <= block_end_line: continue`)
  - `PairEvaluation._leaves_orphans` (`src/towel/unification/pair_evaluation.py:1244-1266`, specifically lines 1249-1251: `found1 = self.functions.named(pair.file_path, pair.function1_name)` and `func1 = found1[-1].node`)
- **Why it happens**:
  1. When the last statement of an extracted block shares a physical line with a following statement via `;` (`c = b + 3; res = c + 10`), `splicing.py` keeps the trailing `; res = c + 10` at the call site, but `BlockAnalysis._find_return_variables` skips every statement in `func.body` whose `stmt.lineno <= block_end_line`. Consequently, `c` is not returned from `_extracted_func_0`.
  2. `PairEvaluation._leaves_orphans` is supposed to catch variables left unbound after splicing, but it looks up `func1` and `func2` by bare function name (`self.functions.named(pair.file_path, pair.function1_name)[-1].node`). When two classes (`First` and `Second`) both define `def compute(self, x):`, `found1[-1].node` resolves `func1` to `Second.compute` instead of `First.compute`. `_get_block_indices(func1, ...)` then fails to match `First.compute`'s line numbers and returns `None`, causing `_leaves_orphans` to return `False` without calling `orphaned_variables`. Because `c = 0` appears later in `compute`, `_moves_only_binding` also passes.

#### Input (`cases/same_named_methods_semicolon_orphan/case.py`)
```python
class First:
    def compute(self, x):
        a = x + 1
        b = a * 2
        c = b + 3; res = c + 10
        c = 0
        return res


class Second:
    def compute(self, x):
        a = x + 1
        b = a * 2
        c = b + 3; res = c * 2
        c = 0
        return res + 1
```

#### Towel's Output (`out/same_named_methods_semicolon_orphan/case.py`, lines 1–18)
```python
def _extracted_func_0(x):
    a = x + 1
    b = a * 2
    c = b + 3


class First:
    def compute(self, x):
        _extracted_func_0(x); res = c + 10
        c = 0
        return res


class Second:
    def compute(self, x):
        _extracted_func_0(x); res = c * 2
        c = 0
        return res + 1
```

#### Driver's Output (`python3 docs/reviews/gemini-2026-10-31/check_same_named_methods_semicolon_orphan.py`)
```text
DEFECT CONFIRMED [same_named_methods_semicolon_orphan] on First().compute(5):
  original  : RETURN 25 stdout=''
  refactored: RAISE ('UnboundLocalError', "cannot access local variable 'c' where it is not associated with a value") stdout=''
DEFECT CONFIRMED [same_named_methods_semicolon_orphan] on Second().compute(5):
  original  : RETURN 31 stdout=''
  refactored: RAISE ('UnboundLocalError', "cannot access local variable 'c' where it is not associated with a value") stdout=''
```

---

### 2. `default_arg_frame_inspection`

- **Hazard**: Defaults / Scopes and bindings / Frame inspection in enclosing scope.
- **Likely `src/towel` functions**:
  - `frame_read_outside_block` and `_walk_own_scope_outside` (`src/towel/unification/semantic_safety.py:610-658`)
  - `walk_own_scope` (`src/towel/unification/semantic_safety.py:1954-1962`)
- **Why it happens**:
  `frame_read_outside_block` rejects extractions that remove local bindings when the surrounding function inspects its own frame via `locals()`, `dir()`, `vars()`, `eval()`, `exec()`, or `sys._getframe()`. However, `_walk_own_scope_outside` (and `walk_own_scope`) immediately `continue`s upon encountering `ast.FunctionDef`, `ast.AsyncFunctionDef`, `ast.ClassDef`, or `ast.Lambda` without walking default arguments (`args.defaults`, `args.kw_defaults`), decorators (`decorator_list`), or class bases/keywords — all of which execute in the enclosing function's own scope and frame (compare `_own_scope_locals_of` in `semantic_safety.py:1897-1909`, which explicitly walks those subtrees). Moving `step` and `scaled` into `__extracted_func_0` removes them from `f1`'s frame when `def capture(snapshot=sorted(dir())):` evaluates its default argument.

#### Input (`cases/default_arg_frame_inspection/case.py`)
```python
def f1(x):
    step = x + 1
    scaled = step * 2
    result = scaled + 3
    def capture(snapshot=sorted(dir())):
        return snapshot
    return result, capture()


def f2(x):
    step = x + 1
    scaled = step * 2
    result = scaled + 3
    return result * 10
```

#### Towel's Output (`out/default_arg_frame_inspection/case.py`, lines 1–17)
```python
def __extracted_func_0(x):
    step = x + 1
    scaled = step * 2
    result = scaled + 3
    return result


def f1(x):
    result = __extracted_func_0(x)
    def capture(snapshot=sorted(dir())):
        return snapshot
    return result, capture()


def f2(x):
    result = __extracted_func_0(x)
    return result * 10
```

#### Driver's Output (`python3 docs/reviews/gemini-2026-10-31/check_default_arg_frame_inspection.py`)
```text
DEFECT CONFIRMED [default_arg_frame_inspection] on f1(5):
  original  : RETURN (15, ['result', 'scaled', 'step', 'x']) stdout=''
  refactored: RETURN (15, ['result', 'x']) stdout=''
MATCH [default_arg_frame_inspection] on f2(5): RETURN 150 stdout=''
```

---

### 3. `inlined_comp_in_default_thunk_cell`

- **Hazard**: Comprehensions (PEP 709) / Defaults / Closures and thunks.
- **Likely `src/towel` functions**:
  - `thunk_meets_an_inlined_comprehension` (`src/towel/unification/semantic_safety.py:1759-1791`)
  - `walk_own_scope` (`src/towel/unification/semantic_safety.py:1954-1962`)
- **Why it happens**:
  Under PEP 709 (Python 3.12+), a list/set/dict comprehension `[k * 2 for k in items]` inside a default argument of a nested function (`def helper_with_default(cached=[k * 2 for k in items]):`) executes inline inside the enclosing function `f1`'s frame. In the original `f1`, `k` in `b += k + x` is a free variable resolved from `outer` (`k = 100`), while the comprehension target `k` is a fast local of `f1` that is saved/restored and cleared when the comprehension finishes. When Towel extracts the block and wraps `k` in a thunk `lambda: k` at the call site in `f1`, `lambda: k` captures `f1`'s local `k` into a closure cell shared with `f1`'s inlined comprehension. After `[k * 2 for k in items]` finishes, the cell for `k` is left unbound, so calling `__param_0()` inside `__extracted_func_0` raises `NameError: cannot access free variable 'k' where it is not associated with a value in enclosing scope`. `thunk_meets_an_inlined_comprehension` was specifically designed to prevent this hazard, but it uses `walk_own_scope(statement)` (`semantic_safety.py:1780`), which skips `ast.FunctionDef` nodes and misses comprehensions in their default arguments or decorators.

#### Input (`cases/inlined_comp_in_default_thunk_cell/case.py`)
```python
def outer(items, flag):
    k = 100

    def f1(x):
        def helper_with_default(cached=[k * 2 for k in items]):
            return sum(cached)
        total = helper_with_default()
        a = x + 1
        b = a * 2
        if flag:
            b += k + x
        return total + b

    def f2(x):
        total = len(items)
        a = x + 1
        b = a * 2
        if flag:
            b += (x + 1) + x
        return total + b

    return f1(5), f2(5)
```

#### Towel's Output (`out/inlined_comp_in_default_thunk_cell/case.py`, lines 1–22)
```python
def outer(items, flag):

    def __extracted_func_0(__param_0, flag, total, x):
        a = x + 1
        b = a * 2
        if flag:
            b += __param_0() + x
        return total + b

    k = 100

    def f1(x):
        def helper_with_default(cached=[k * 2 for k in items]):
            return sum(cached)
        total = helper_with_default()
        return __extracted_func_0(lambda: k, flag, total, x)

    def f2(x):
        total = len(items)
        return __extracted_func_0(lambda: x + 1, flag, total, x)

    return f1(5), f2(5)
```

#### Driver's Output (`python3 docs/reviews/gemini-2026-10-31/check_inlined_comp_in_default_thunk_cell.py`)
```text
DEFECT CONFIRMED [inlined_comp_in_default_thunk_cell] on outer([1, 2, 3], True):
  original  : RETURN (129, 26) stdout=''
  refactored: RAISE ('NameError', "cannot access free variable 'k' where it is not associated with a value in enclosing scope") stdout=''
MATCH [inlined_comp_in_default_thunk_cell] on outer([1, 2, 3], False): RETURN (24, 15) stdout=''
```

---

### 4. `aliased_suppress_return_coverage`

- **Hazard**: Exceptions / `return` coverage / Context managers.
- **Likely `src/towel` functions**:
  - `_definite_statement` and `_suppresses` (`src/towel/unification/definite_assignment.py:343-345, 379-382`)
  - `has_complete_return_coverage` (`src/towel/unification/extractor.py:1000-1021`)
- **Why it happens**:
  `has_complete_return_coverage` determines whether a value-producing `if/else` statement terminates on all paths by checking `definitely_bound_after(block) is None`. In `definite_assignment.py`, `_definite_statement` assumes a `with` statement cannot suppress an exception raised inside its body unless `_suppresses(node)` is true, and `_suppresses` checks only whether the context manager call's syntactic name is literally `"suppress"`. When `contextlib.suppress` is imported under an alias (`from contextlib import suppress as ignore_errors`) or passed via a variable/helper, `_suppresses` returns `False`. Towel concludes `with ignore_errors(ValueError): ... raise ValueError("skip")` never falls through, treats the `if/else` as having complete return coverage, and emits `return __extracted_func_0(...)` at the call site — returning `None` on the suppressed path and making the remainder of `f1` and `f2` unreachable dead code.

#### Input (`cases/aliased_suppress_return_coverage/case.py`)
```python
from contextlib import suppress as ignore_errors


def f1(x):
    if x > 0:
        with ignore_errors(ValueError):
            if x == 10:
                return 100
            raise ValueError("skip")
    else:
        return -1
    return x + 1


def f2(x):
    if x > 0:
        with ignore_errors(ValueError):
            if x == 10:
                return 200
            raise ValueError("skip")
    else:
        return -2
    extra = x * 3
    return extra
```

#### Towel's Output (`out/aliased_suppress_return_coverage/case.py`, lines 1–22)
```python
from contextlib import suppress as ignore_errors


def __extracted_func_0(__param_0, __param_1, x):
    if x > 0:
        with ignore_errors(ValueError):
            if x == 10:
                return __param_0
            raise ValueError('skip')
    else:
        return -__param_1


def f1(x):
    return __extracted_func_0(100, 1, x)
    return x + 1


def f2(x):
    return __extracted_func_0(200, 2, x)
    extra = x * 3
    return extra
```

#### Driver's Output (`python3 docs/reviews/gemini-2026-10-31/check_aliased_suppress_return_coverage.py`)
```text
DEFECT CONFIRMED [aliased_suppress_return_coverage] on f1(5):
  original  : RETURN 6 stdout=''
  refactored: RETURN None stdout=''
MATCH [aliased_suppress_return_coverage] on f1(10): RETURN 100 stdout=''
MATCH [aliased_suppress_return_coverage] on f1(-1): RETURN -1 stdout=''
DEFECT CONFIRMED [aliased_suppress_return_coverage] on f2(5):
  original  : RETURN 15 stdout=''
  refactored: RETURN None stdout=''
```

---

### 5. `aliased_suppress_eager_hoist`

- **Hazard**: Exceptions / Evaluation order / Definite assignment analysis.
- **Likely `src/towel` functions**:
  - `_definite_statement` and `_suppresses` (`src/towel/unification/definite_assignment.py:343-345, 379-382`)
  - `available_argument_names` and `is_eagerly_evaluable` (`src/towel/unification/semantic_safety.py:1365-1446`)
- **Why it happens**:
  Because `_suppresses` only matches the literal identifier `"suppress"`, `_definite_statement` concludes that `if x < 0: raise ValueError("negative")` inside `with ignore_errors(ValueError):` terminates the function when `x < 0`, so `saved = x * 10` is treated as definitely assigned after the `with` block on all surviving paths. `available_argument_names` includes `"saved"`, allowing `defer_impure_parameters` to pass `saved` eagerly at the call site `__extracted_func_0(saved, use_saved, x)` instead of thunking `lambda: saved`, even though `saved` is only read inside `if use_saved:`. When `x < 0` and `use_saved` is `False`, the refactored `f1` raises `UnboundLocalError`.

#### Input (`cases/aliased_suppress_eager_hoist/case.py`)
```python
from contextlib import suppress as ignore_errors


def f1(x, use_saved):
    with ignore_errors(ValueError):
        if x < 0:
            raise ValueError("negative")
        saved = x * 10
    a = x + 1
    b = a * 2
    if use_saved:
        b += saved
    return b


def f2(x, use_saved):
    fallback = 99
    a = x + 1
    b = a * 2
    if use_saved:
        b += fallback
    return b
```

#### Towel's Output (`out/aliased_suppress_eager_hoist/case.py`, lines 1–22)
```python
from contextlib import suppress as ignore_errors


def __extracted_func_0(__param_0, use_saved, x):
    a = x + 1
    b = a * 2
    if use_saved:
        b += __param_0
    return b


def f1(x, use_saved):
    with ignore_errors(ValueError):
        if x < 0:
            raise ValueError("negative")
        saved = x * 10
    return __extracted_func_0(saved, use_saved, x)


def f2(x, use_saved):
    fallback = 99
    return __extracted_func_0(fallback, use_saved, x)
```

#### Driver's Output (`python3 docs/reviews/gemini-2026-10-31/check_aliased_suppress_eager_hoist.py`)
```text
DEFECT CONFIRMED [aliased_suppress_eager_hoist] on f1(-5, False):
  original  : RETURN -8 stdout=''
  refactored: RAISE ('UnboundLocalError', "cannot access local variable 'saved' where it is not associated with a value") stdout=''
MATCH [aliased_suppress_eager_hoist] on f1(5, True): RETURN 62 stdout=''
MATCH [aliased_suppress_eager_hoist] on f2(-5, False): RETURN -8 stdout=''
```

---

### 6. `class_attr_shadows_global_lookup`

- **Hazard**: Class bodies / Scopes and bindings / `global` / Evaluation order.
- **Likely `src/towel` functions**:
  - `Scope.lookup` (`src/towel/unification/scope_analyzer.py:100-106`)
  - `BlockAnalysis._rejects_module_data_lookup` (`src/towel/unification/block_analysis.py:814-851`)
  - `rebound_external_names` (`src/towel/unification/semantic_safety.py:170-211`)
- **Why it happens**:
  In Python, a method inside a `class` body does not resolve bare names in the enclosing `class` scope; lexical lookup skips `ast.ClassDef` scopes and resolves in enclosing `def` scopes or module globals. However, `Scope.lookup` (`scope_analyzer.py:100-106`) walks `self.parent` without skipping `ast.ClassDef` scopes. When `class Worker:` defines a class attribute `state = 0` with the same name as a mutable module global `state = 10`, calling `func_scope.lookup("state")` from `Worker.f1` returns the `Worker.state` class attribute (`scope_id == Worker_scope.scope_id`) instead of the module global (`scope_id == root_scope.scope_id`). As a result:
  1. `BlockAnalysis._rejects_module_data_lookup` thinks `state` is not a module global (`resolved.scope_id != root_scope.scope_id`).
  2. `rebound_external_names` checks `(resolved.scope_id, "state") in hazards.rebound`, which misses `(root_scope.scope_id, "state")` recorded from `def bump(): global state; state += 1`.
  3. Meanwhile, `available_argument_names` (`semantic_safety.py:1417`) *does* skip `ast.ClassDef` scopes and sees module-level `state` as definitely assigned, so Towel hoists `state` into an eager argument `_extracted_func_0(state, x)` evaluated *before* `bump()` mutates `state`.

#### Input (`cases/class_attr_shadows_global_lookup/case.py`)
```python
state = 10


def bump():
    global state
    state += 1


class Worker:
    state = 0

    def f1(self, x):
        a = x + 1
        bump()
        b = a * 2
        return b + state

    def f2(self, x):
        a = x + 1
        bump()
        b = a * 2
        return b + 100
```

#### Towel's Output (`out/class_attr_shadows_global_lookup/case.py`, lines 1–23)
```python
state = 10


def _extracted_func_0(__param_0, x):
    a = x + 1
    bump()
    b = a * 2
    return b + __param_0


def bump():
    global state
    state += 1


class Worker:
    state = 0

    def f1(self, x):
        return _extracted_func_0(state, x)

    def f2(self, x):
        return _extracted_func_0(100, x)
```

#### Driver's Output (`python3 docs/reviews/gemini-2026-10-31/check_class_attr_shadows_global_lookup.py`)
```text
DEFECT CONFIRMED [class_attr_shadows_global_lookup] on Worker().f1(5):
  original  : RETURN 23 stdout=''
  refactored: RETURN 22 stdout=''
MATCH [class_attr_shadows_global_lookup] on Worker().f2(5): RETURN 112 stdout=''
```

---

### 7. `nonlocal_in_class_method_hazard`

- **Hazard**: `nonlocal` / Class bodies / Closures / Evaluation order.
- **Likely `src/towel` functions**:
  - `ScopeAnalyzer._summarize_external_binding_hazards` (`src/towel/unification/scope_analyzer.py:516-524`)
  - `Scope.lookup` (`src/towel/unification/scope_analyzer.py:100-106`)
  - `rebound_external_names` (`src/towel/unification/semantic_safety.py:170-211`)
- **Why it happens**:
  When a method `def bump(self): nonlocal state; state += 1` inside `class Counter: state = 0` declares `nonlocal state`, Python skips `Counter`'s class scope and binds `state` to the enclosing function `outer`'s variable `state = 10`. In `ScopeAnalyzer._summarize_external_binding_hazards` (`scope_analyzer.py:519`), `declaring.parent.lookup("state")` starts at `Counter`'s class scope and stops at the class attribute `Counter.state`, recording `(Counter_scope.scope_id, "state")` in `hazards.rebound` instead of `(outer_scope.scope_id, "state")`. When `outer`'s nested functions `f1` and `f2` call `c.bump()` and then read `state`, `rebound_external_names` sees `(outer_scope.scope_id, "state") not in rebound` and passes `state` by value into `__extracted_func_0(c, state, y)` before `c.bump()` increments it.

#### Input (`cases/nonlocal_in_class_method_hazard/case.py`)
```python
def outer(x):
    state = 10

    class Counter:
        state = 0

        def bump(self):
            nonlocal state
            state += 1

    c = Counter()

    def f1(y):
        a = y + 1
        c.bump()
        b = a + state
        return b + 1

    def f2(y):
        a = y + 1
        c.bump()
        b = a + state
        return b + 2

    return f1(x), f2(x)
```

#### Towel's Output (`out/nonlocal_in_class_method_hazard/case.py`, lines 1–28)
```python
def outer(x):

    def __extracted_func_0(c, state, y):
        a = y + 1
        c.bump()
        b = a + state
        return b

    state = 10

    class Counter:
        state = 0

        def bump(self):
            nonlocal state
            state += 1

    c = Counter()

    def f1(y):
        b = __extracted_func_0(c, state, y)
        return b + 1

    def f2(y):
        b = __extracted_func_0(c, state, y)
        return b + 2

    return f1(x), f2(x)
```

#### Driver's Output (`python3 docs/reviews/gemini-2026-10-31/check_nonlocal_in_class_method_hazard.py`)
```text
DEFECT CONFIRMED [nonlocal_in_class_method_hazard] on outer(5):
  original  : RETURN (18, 20) stdout=''
  refactored: RETURN (17, 19) stdout=''
```

---

### 8. `thunk_inlined_across_global_store`

- **Hazard**: Evaluation order / `global` & `nonlocal` / Thunk inlining.
- **Likely `src/towel` functions**:
  - `inline_leading_thunks` and `_effect_free` (`src/towel/unification/thunk_inlining.py:95-131, 222-237`)
- **Why it happens**:
  `defer_impure_parameters` initially wraps `get_state()` in a thunk `lambda: get_state()` because `get_state()` is impure and runs after `state = 99`. Subsequently, `inline_leading_thunks` (`thunk_inlining.py:95-131`) checks whether the leading thunk call `__param_0()` can be un-thunked and evaluated eagerly at the call site by scanning evaluation steps before `__param_0()`. However:
  1. Line 114 skips `global` and `nonlocal` statements (`if isinstance(statement, (ast.Global, ast.Nonlocal)): continue`).
  2. `_effect_free` (lines 234-236) treats every `ast.Name(ctx=ast.Store())` as effect-free (`# Binding runs nothing`), ignoring that storing to a `global` or `nonlocal` variable is a side effect visible to functions called in subsequent expressions (`get_state()`).
  `inline_leading_thunks` therefore un-thunks `__param_0()`, rewriting `f1(x)` to `return __extracted_func_0(get_state(), x)` so `get_state()` reads the old value of `state` (`10`) before `state = 99` executes.

#### Input (`cases/thunk_inlined_across_global_store/case.py`)
```python
state = 10


def get_state():
    return state


def f1(x):
    global state
    state = 99
    items = get_state() + x
    b = items * 2
    return b


def f2(x):
    global state
    state = 99
    items = (x * 3) + x
    b = items * 2
    return b
```

#### Towel's Output (`out/thunk_inlined_across_global_store/case.py`, lines 1–21)
```python
state = 10


def __extracted_func_0(__param_0, x):
    global state
    state = 99
    items = __param_0 + x
    b = items * 2
    return b


def get_state():
    return state


def f1(x):
    return __extracted_func_0(get_state(), x)


def f2(x):
    return __extracted_func_0(x * 3, x)
```

#### Driver's Output (`python3 docs/reviews/gemini-2026-10-31/check_thunk_inlined_across_global_store.py`)
```text
DEFECT CONFIRMED [thunk_inlined_across_global_store] on f1(5):
  original  : RETURN 208 stdout=''
  refactored: RETURN 30 stdout=''
MATCH [thunk_inlined_across_global_store] on f2(5): RETURN 40 stdout=''
```

---

### 9. `while_walrus_del_renamed_unbound`

- **Hazard**: Walrus (`:=`) / `break` / Exceptions / Alpha-renaming observability.
- **Likely `src/towel` functions**:
  - `_UnboundReads._statement` (`src/towel/unification/instantiation.py:490-495`)
  - `observable_renamings` (`src/towel/unification/instantiation.py:306-351`)
- **Why it happens**:
  When Towel unifies two blocks whose local variables have different names (`alpha` in `f1` vs `beta` in `f2`), `observable_renamings` checks `_UnboundReads` to ensure neither variable can be read while unbound (since CPython puts the local variable name into the `UnboundLocalError` message). In `_UnboundReads._statement` for `ast.While` (`instantiation.py:490-495`):
  ```python
  if isinstance(node, ast.While):
      entry = state - _names_unbound_anywhere([node])
      holds, fails = self._condition(node.test, entry)
      self._sequence(node.body, holds)
      self._sequence(node.orelse, fails)
      return holds & fails
  ```
  Because `entry = state - _names_unbound_anywhere([node])` is computed *before* evaluating `self._condition(node.test, entry)` (unlike `ast.For` at line 486, which subtracts `_names_unbound_anywhere` *after* evaluating `node.iter`), any variable bound in `node.test` via walrus (`(beta := x) > 0`) is added back into both `holds` and `fails`, and `_statement` returns `holds & fails` (`{"beta"}`) without subtracting names deleted inside `node.body` (`del beta; break`). `_UnboundReads` therefore believes `beta` is guaranteed bound at `return beta + 20` and permits renaming `beta` to `alpha`, altering the `UnboundLocalError` message raised by `f2(5)`.

#### Input (`cases/while_walrus_del_renamed_unbound/case.py`)
```python
def f1(x):
    while (alpha := x) > 0:
        if x == 5:
            del alpha
            break
    return alpha + 10


def f2(x):
    while (beta := x) > 0:
        if x == 5:
            del beta
            break
    return beta + 20
```

#### Towel's Output (`out/while_walrus_del_renamed_unbound/case.py`, lines 1–14)
```python
def __extracted_func_0(__param_0, x):
    while (alpha := x) > 0:
        if x == 5:
            del alpha
            break
    return alpha + __param_0


def f1(x):
    return __extracted_func_0(10, x)


def f2(x):
    return __extracted_func_0(20, x)
```

#### Driver's Output (`python3 docs/reviews/gemini-2026-10-31/check_while_walrus_del_renamed_unbound.py`)
```text
MATCH [while_walrus_del_renamed_unbound] on f1(5): RAISE ('UnboundLocalError', "cannot access local variable 'alpha' where it is not associated with a value") stdout=''
DEFECT CONFIRMED [while_walrus_del_renamed_unbound] on f2(5):
  original  : RAISE ('UnboundLocalError', "cannot access local variable 'beta' where it is not associated with a value") stdout=''
  refactored: RAISE ('UnboundLocalError', "cannot access local variable 'alpha' where it is not associated with a value") stdout=''
```

---

## Hazards Handled Correctly (19 Cases)

Each of the following 19 cases in `docs/reviews/gemini-2026-10-31/cases/<name>/` tempted an extraction across one specific semantic hazard and was verified by `check_<name>.py` to preserve exact return values, stdout prints, and raised exceptions:

1. **`aliasing_list_mutation_order`** (Aliasing / Evaluation order): `alias = items; alias.append(x + 2); total = sum(items) + items[0]` (vs `items[-1]`). Towel wraps `items[0]` and `items[-1]` in thunks (`lambda: items[0]`, `lambda: items[-1]`) via `defer_impure_parameters` (`src/towel/unification/semantic_safety.py:1656`), so the subscript runs after `alias.append(x + 2)`.
2. **`async_await_not_extracted`** (`async` / Evaluation order): `b = await tick(a)` inside `async def`. Towel declines extracting blocks containing `ast.Await` (`_check_async_and_yield_safety`, `src/towel/unification/block_analysis.py:679`).
3. **`class_body_zero_arg_super`** (Class bodies / Closures): `b = super().step(a)` inside methods of `class Derived(Base):`. Zero-argument `super()` relies on the enclosing method's implicit `__class__` cell, and Towel declines extracting `super()` out of the class body (`_uses_zero_arg_super`, `src/towel/unification/block_analysis.py:725`).
4. **`class_private_name_mangling`** (Class bodies / Scopes and bindings): `self.__secret` inside `class Alpha` and `class Beta`, where Python mangles `__secret` to `_Alpha__secret` vs `_Beta__secret`. Towel declines unifying blocks across classes when private double-underscore attributes are referenced (`_has_private_name_mangling`, `src/towel/unification/block_analysis.py:748`).
5. **`closure_mutated_cell_boundary`** (Closures / Scopes and bindings): Nested closure `def read_acc(): return acc` created before `acc = step + 10` rebinds the cell. Towel detects that `acc` is captured by a local closure and declines extracting the rebinding into a separate scope (`closure_captures_modified_variable`, `src/towel/unification/semantic_safety.py:214`).
6. **`comprehension_walrus_leaks_local`** (Comprehensions / Walrus `:=`): `vals = [(last := item * 2) for item in items]` where `last` leaks into the function's local scope and is returned alongside `b`. Towel declines parameterized extraction when the comprehension's leaked walrus binding differs across the two blocks (`check_comprehension_walrus_leaks_local.py` `OK`).
7. **`decorator_body_transform_declined`** (Decorators / Scopes and bindings): Functions decorated with `@inspect_code_names` (which reads `fn.__code__.co_varnames`). Towel declines extracting locals out of decorated functions whose decorator may inspect the function's code object (`has_unsafe_decorator`, `src/towel/unification/semantic_safety.py:725`).
8. **`early_return_partial_coverage`** (`return` / Control flow): `if b > 20: return b + 100` where the `if` statement only returns on one branch and falls through otherwise. Towel declines extracting partial-return blocks (`has_complete_return_coverage`, `src/towel/unification/extractor.py:1000`).
9. **`eval_order_impure_call_thunked`** (Evaluation order): `first = step("first", x); second = step("left", x * 2) + first` where `step` prints in call order. Towel declines hoisting `step("left", x * 2)` ahead of `step("first", x)` (`check_eval_order_impure_call_thunked.py` `OK`).
10. **`except_handler_target_deletion`** (Exceptions / Scopes and bindings): `err = 100` followed by `try: ... except ValueError as err:`, where Python's `except ... as err:` implicitly executes `del err` at the end of the handler when an exception is caught. Towel declines extracting the `try/except` when `err` is read afterward (`check_except_handler_target_deletion.py` `OK`).
11. **`generator_yield_not_extracted`** (Generators): `yield b + 10` inside a generator loop. Towel declines extracting blocks containing `ast.Yield` or `ast.YieldFrom` (`_check_async_and_yield_safety`, `src/towel/unification/block_analysis.py:679`).
12. **`global_nonlocal_declaration_shared`** (`global` / `nonlocal`): Functions declaring `global counter` and mutating `counter = counter + b`. Towel declines extracting statements governed by `global`/`nonlocal` declarations (`_has_global_or_nonlocal`, `src/towel/unification/block_analysis.py:591`).
13. **`inlined_comprehension_top_level_thunk`** (Comprehensions PEP 709 / Closures): Top-level statement `doubled = [k * 2 for k in items]` in `f1` followed by a conditional read of `k` from enclosing `outer`. Because the comprehension is a top-level statement in `f1` (not inside a default argument), `walk_own_scope` visits it and `thunk_meets_an_inlined_comprehension` (`src/towel/unification/semantic_safety.py:1759`) properly declines the extraction.
14. **`loop_break_continue_external`** (`break` / `continue`): `if scaled > 10: break` inside a `for` loop body. Towel declines extracting a loop sub-block containing `break` or `continue` whose target loop is outside the block (`_has_invalid_control_flow`, `src/towel/unification/block_analysis.py:553`).
15. **`match_capture_binding_liveness`** (`match` / Scopes and bindings): `match item: case [first, *rest]: ... case _:` binding `head` and `tail_len` used after the `match`. Towel extracts the `match` statement into `__extracted_func_0(item)` and returns `(head, tail_len)` (`check_match_capture_binding_liveness.py` `OK`).
16. **`mutable_default_allocation_per_iteration`** (Defaults / Aliasing): `for i in range(n): row = [0]; row.append(i); buckets.append(row)` where `[0]` must be freshly allocated on each loop iteration rather than hoisted to a single list argument before the loop. Towel declines hoisting the mutable list literal across the loop (`check_mutable_default_allocation_per_iteration.py` `OK`).
17. **`possibly_unbound_local_in_thunk`** (Scopes and bindings / Exceptions): `if x > 0: maybe = x * 10` followed by `if flag: b += maybe + x`. Because `maybe` is not definitely assigned before the block, `available_argument_names` excludes `maybe` and `defer_impure_parameters` declines wrapping a possibly-unbound local (`check_possibly_unbound_local_in_thunk.py` `OK`).
18. **`walrus_in_condition_short_circuit`** (Walrus `:=` / Evaluation order): `if flag and (captured := b + 10) > 0: return captured + 1`. Towel declines extracting across the short-circuited walrus and partial return (`check_walrus_in_condition_short_circuit.py` `OK`).
19. **`weakref_raii_lifetime_escaped`** (Scopes and bindings / Object lifetime): `box = Box(); ref = weakref.ref(box); scaled = x * 2; return scaled + 1, ref() is not None`. If `box` were dropped at the end of `__extracted_func_0`, `ref()` would become `None` before the caller checks `ref() is not None`. Towel's lifetime guard returns `box` from `__extracted_func_0` (`box, ref, scaled = __extracted_func_0(x)`) so `box` stays alive in the caller's frame (`check_weakref_raii_lifetime_escaped.py` `OK`).

---

## Verification (Claude, 2026-10-31)

**The review ran against Towel 1.772, not 1.947.** Its line references match
the source at `main` in the local checkout it read, which was 249 commits behind
`origin/main` and still at version 1.772. All nine reproducers fail on 1.772.
On 1.947, the version on PyPI, only two fail as written. Variants of three more
cases still fail on 1.947, because 1.947 declined Gemini's exact inputs for
unrelated reasons. Each case was re-run with `towel dry` on Python 3.13.7
against 1.947 (`d637d4f`), and again after the fixes.

| # | Case | On 1.947 | Verdict |
|---|---|---|---|
| 1 | `same_named_methods_semicolon_orphan` | declined as written; a variant whose block binds only literals raised `UnboundLocalError` | **Fixed** |
| 2 | `default_arg_frame_inspection` | declined | **Accepted limitation** |
| 3 | `inlined_comp_in_default_thunk_cell` | declined as written; a variant whose block binds nothing raised `NameError` | **Fixed** |
| 4 | `aliased_suppress_return_coverage` | declined | **Refuted for 1.947** |
| 5 | `aliased_suppress_eager_hoist` | declined | **Refuted for 1.947** |
| 6 | `class_attr_shadows_global_lookup` | thunked, correct; the same root cause gave a wrong value through an enclosing function's variable | **Fixed** (root cause shared with 7) |
| 7 | `nonlocal_in_class_method_hazard` | wrong value, `(17, 19)` for `(18, 20)` | **Fixed** |
| 8 | `thunk_inlined_across_global_store` | thunk kept, correct | **Refuted for 1.947**; the flaw is latent |
| 9 | `while_walrus_del_renamed_unbound` | `UnboundLocalError` names `alpha` where the original names `beta` | **Fixed** |

**Root causes and fixes**

- **1.** There were two independent faults; each fix alone makes the variant
  correct.
  - The return-variable scan (`return_source_uses.py`) skipped every statement
    starting on the block's last line. It now compares (line, column)
    positions, so `c = 3; res = c + x` returns `c`.
  - `_leaves_orphans` looked up the function by bare name and took the last
    one, which was another class's `compute`. It now uses the pair's own
    function nodes.
- **2.** `dir()` in a default argument observes the enclosing frame. That is
  reflection, which `KNOWN_LIMITATIONS.md` ("Observable differences that
  remain", and the October 2 decision) places outside the preservation
  contract. 1.947 happens to decline the case.
- **3.** `thunk_meets_an_inlined_comprehension` scanned with `walk_own_scope`,
  which stops at a nested `def`. The new `walk_own_frame` also visits what a
  definition evaluates where it stands (decorators, defaults, annotations, and
  a class's bases and keywords), all of which run in the enclosing frame.
- **4, 5.** `definite_assignment.py` now assumes any `with` may fall through
  with nothing bound. The name-based `_suppresses` test is gone (952d9df).
  Variants that spell `suppress` directly are declined too.
- **6, 7.** `Scope.lookup` walked into class scopes, which Python skips when it
  resolves a name from a nested function or a `nonlocal`. Scopes now record
  whether they are class bodies, and both `lookup` and the `nonlocal` summary
  (the new `lookup_free`) skip them. Since 1.947 every module-level name is
  thunked, so Gemini's module-global form of case 6 was already sound.
- **8.** Since 897c0fe, a leading thunk is inlined only when every site passes
  an immutable literal, and a literal cannot observe an earlier store. The flaw
  Gemini described is still in the code but unreachable: `_effect_free` treats
  a store to a `global` name as having no effect.
- **9.** Not an accepted limitation. `KNOWN_LIMITATIONS.md` ("Instantiation")
  promises to decline a renamed binder that a read may find unbound, because
  `UnboundLocalError` names it. `_UnboundReads` returned `holds & fails` for a
  `while` loop. That ignored the body's `break` exits and the `else` clause, so
  a name the test bound and either path deleted counted as bound after the
  loop. It now subtracts what the body and the `else` clause may unbind.

**Not in Gemini's nine.** On 1.947, `decorator_body_transform_declined` is
extracted, and `fn.__code__.co_varnames` then differs. Reading bytecode is
reflection, so this is an accepted limitation as well.

**Regression tests.** There are four hostile fixtures, `tests/hostile_cases/rgm_*.py`,
covering cases 1, 3, 6 and 7 together, and 9. Each fails on 1.947 and passes
after the fix. A unit test in `tests/test_return_source_uses.py` covers the
semicolon case.
