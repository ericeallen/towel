# Trusted decorators

[Proposals and experiments](README.md) · [Documentation index](../README.md)

Status: retired October 2, 2026. The owner removed reflection and body
instrumentation from the preservation guarantee, including protections based
on decorator allowlists. A trust override is therefore unnecessary. See the
[October 2 decision](../DECISIONS.md#2026-10-02-reflection-and-self-instrumentation-are-outside-the-preservation-contract).

The September 24 proposal below is retained as historical design context; it
does not describe the current contract or available options.

## The problem

From 1.772, code moves out of a decorated function only when every
decorator that can reach it is known to leave bodies alone. Known means one
of two things:
- the decorator is on a curated list, each entry verified against the
  library's source;
- it is a project decorator that Towel can show is a plain wrapper.

Any other decorator declines the pair (see `docs/DECISIONS.md`, "Code under
a decorator moves only if the decorator leaves bodies alone"). A framework
whose decorators are not yet on the list, a route or a command registry for
instance, then gets no extraction from the functions it decorates.

## The proposal

Let the user name decorators they trust, as an escape hatch:

- `--trust-decorator ORIGIN`, repeatable, and
  `[tool.towel] trusted-decorators = [...]` in `pyproject.toml`, so a
  team's runs and its CI agree.
- A decorator is named by its canonical origin (`myframework.routes.route`),
  resolved by binding as the built-in list is, never by how a file spells
  it. A factory is named by the callable it calls.
- Trusting a decorator states a precise claim that Towel cannot check. The
  decorator never reads or rewrites the decorated function's code, source,
  globals or closure, and at most calls the function with its own
  arguments. The documentation must say exactly that.
- Every run lists the trusted decorators it relied on, and the proposals
  each one admitted, so the trust shows in the output instead of hiding in
  it.
- Entries that users commonly trust become candidates for the verified
  built-in list, each added only once its library source has been read.

## Open questions

- Whether a trusted decorator should also admit placing a helper inside the
  decorated function or class, or only moving code out of it.
- Whether trust may be scoped to part of the tree, for example to exclude
  tests.
