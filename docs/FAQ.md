# Frequently asked questions

## Will Towel change my files without asking?

Not without asking. `towel preview` never writes. `towel dry` changes code only
after you answer `y`, or when you pass `--no-interactive`, and given a separate
output directory it leaves the input untouched. When there is nothing to change,
`dry` asks nothing, and a separate output directory still receives a copy of the
input:

```bash
towel preview shop/                 # show the change; write nothing
towel dry shop/ cleaned/            # write a changed copy to cleaned/
towel dry shop/ shop/               # change shop/ in place, on a branch
```

## Does Towel run my code?

Towel itself never imports or runs your code; it reads it. It does run a type
checker (mypy or Pyright) on the changed code when one is installed where
Towel runs, and a formatter (Ruff or Black) when the project uses one or its
files already match one, as you would. One exception: mypy plugins your project configures, such as
django-stubs' or pydantic's, run code your project names, as they do whenever
you run mypy. Towel itself uses no network and no AI service.

## How do I undo a run?

Work on a Git branch: `git restore .` undoes the changes to tracked files, and
`.towel-helpers.json`, which Git does not track, is yours to delete. Without
Git, write into a new directory (`towel dry src/ cleaned/`) and copy back only
what you want. If a run is interrupted while writing, give `towel recover` the
`.towel-transaction-<id>` journal it left. It puts every file back, unless you
have edited one since, in which case it keeps your edit and the journal and
names the file ([recovery](CLI_GUIDE.md#recovery-and-interrupted-writes)).

## Why did Towel leave this code alone?

A run that extracts nothing says why, in a sentence per reason. After a run that
extracted something, add `--explain` to see the similar code it declined.
[Why something was left alone](WHY_LEFT_ALONE.md) explains each reason and what
you can do. The most common is that a helper would not be smaller than the
copies it replaces.

## Why is there a `lambda` in the call?

The argument has to run where the original expression ran, not before the
helper starts: after a call, inside a loop, or only under a condition.
[Reading Towel's output](GENERATED_CODE.md#a-lambda-that-delays-an-argument)
shows an example.

## What are `_towel_arguments` and `_towel_owner`?

Strict mode's bookkeeping, which keeps each argument alive exactly as long as
before. You see it only with `--strict`
([relaxed and strict](RELAXED_AND_STRICT.md#what-strict-mode-writes)).

## Is relaxed mode safe for my program?

For nearly every program, yes. These are the conditions:

**What Towel promises.** A change Towel makes keeps what your program
computes and does: its results, output, exceptions and the order of its side
effects. That holds for programs that do not use reflection or
self-instrumentation, including through code they call. Reflection here means
reading frames, source or line numbers, as `inspect.stack()` does, or a log
format with `%(funcName)s`; self-instrumentation means a tool that rewrites
functions, such as pytest's assertion rewriting. Mark a function that depends
on these with `# towel: no-extract`. In relaxed mode, the default, Towel also
assumes that your program does not depend on when Python frees an object it no
longer uses; that reading an attribute, indexing, comparing and arithmetic have
no side effects; and that a module's functions, classes and imports are not
rebound while a helper runs, as a test's monkeypatch can do. `--strict` makes
none of these three assumptions. A change that breaks a program meeting these
conditions is a bug.

Relaxed mode's assumptions reach a little further than that summary. Testing a
value's truth (`__bool__` or `__len__`) must have no side effects and give the
same answer twice. Nothing may depend on the `__qualname__` of a lambda that
uses only its own parameters. With `--cross-module`, code outside the project
must not rebind a builtin in one module, and equal literals must not be
compared with `is`. `--strict` assumes none of these.

When Towel sees code that may depend on when objects are freed, such as a
`__del__` method, the run says so.
[Relaxed and strict](RELAXED_AND_STRICT.md#which-to-use) says when to use
`--strict`.

## Why are the helpers called `__extracted_func_0`?

Towel cannot know what a block means, only that it repeats.
`towel rename-helpers` renames helpers and their parameters in one step,
updating every call ([naming guide](NAMING.md)). You can write the names
yourself or ask an AI assistant to propose them.

## Why is my run slow?

Type checking each change is most of the time on a typed project, where
`--no-types` is several times faster. `--time-limit` bounds any run; a single module takes
seconds. [Performance](PERFORMANCE.md) has typical times.

## Can I keep only some of the changes?

Yes, with Git: `git restore` a file, or stage the hunks you want with
`git add -p` and discard the rest
([using Towel on a project](USING_TOWEL.md#keep-some-changes-and-drop-others)).

## Can Towel fail a build when there is duplicated code?

`towel preview . --check` exits 3 when it would change code, and 0 when it
would not. There is a pre-commit hook too ([recipes](RECIPES.md)).

## Can I put my options in a file?

Yes, under `[tool.towel]` in `pyproject.toml`
([project settings](RECIPES.md#project-settings)).

## Which Python versions does Towel support?

Towel runs on Python 3.11 to 3.15, on macOS and Linux. It reads code with the
interpreter it runs on, so code it should read must parse on that version. A
file that does not is skipped with a warning; with `--strict` the run stops
instead. Windows is not supported.

## My script used `--summ` and now fails. Why?

Towel 2.0 accepts only full option names, so that a future option cannot change
what an abbreviation means. Use `--summary`
([changes in 2.0](https://github.com/ericeallen/towel/blob/main/CHANGELOG.md)).

## Where do I ask a question or report a bug?

See [getting help](GETTING_HELP.md).

[Documentation index](README.md)
