# Callee-Parameter Thunking in Extracted Functions

When unifying two blocks, we sometimes parameterize a callee expression (the thing being called) so the extracted function contains a call like:

```
__param_0(...)
```

This occurs, for example, when the original code differs only by attribute calls such as `item.upper()` vs `item.lower()` under a type-guarded branch.

## Problem

If we pass the callee expression directly as an argument (e.g., `item.upper`) from the call site, it can be eagerly evaluated or bound in a way that bypasses the original type checks, leading to errors like:

- AttributeError: `'int' object has no attribute 'upper'`
- TypeError: zero-arg lambdas receiving unexpected positional arguments

These arise because the callee can be resolved before the extracted function’s control flow (e.g., the `if isinstance(item, str)` guard) is applied, or because the argument arity at the call site doesn’t match the callee’s signature.

## Solution: Forwarding Lambda Thunks

When a unified parameter is used as a callee inside the extracted function body (i.e., appears in `Call.func` position), we pass it from the call site as a forwarding lambda (a “thunk”) that defers evaluation and preserves arity:

```
lambda *args, **kwargs: expr(*args, **kwargs)
```

- Defer evaluation: The callee is only invoked when the extracted function reaches the guarded call site (e.g., inside the string branch), preserving original semantics.
- Preserve arity: The forwarding lambda accepts arbitrary positional/keyword arguments and forwards them to the original expression, avoiding unexpected TypeError from mismatched parameters.

This behavior is implemented by:

- Detecting parameters used as callees in the extracted body
- Wrapping the corresponding call-site arguments with a forwarding lambda

## Example

Original difference:

- Block A: `output["strings"].append(item.upper())`
- Block B: `output["strings"].append(item.lower())`

Extracted function contains:

```
if isinstance(item, str):
    output["strings"].append(__param_0())
```

Call sites pass:

- A: `lambda *args, **kwargs: item.upper(*args, **kwargs)`
- B: `lambda *args, **kwargs: item.lower(*args, **kwargs)`

This ensures attribute access is only performed in the string branch.

## Status

- Observational equivalence: 116/116 proposals pass across all examples
- Cross-file projects: 3/3 pass
- Unit/integration tests: 491 OK

This rule is narrow, hygienic, and maintains referential transparency.
