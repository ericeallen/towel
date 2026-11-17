# Frequently asked questions

[Documentation index](README.md)

## Will Towel change my files without asking?

No. `towel preview` never writes. `towel dry` writes only after you answer
`y`, or when you pass `--no-interactive`, and given a separate output
directory it leaves the input untouched:

```bash
towel preview shop/                 # show the change; write nothing
towel dry shop/ cleaned/            # write a changed copy to cleaned/
towel dry shop/ shop/               # change shop/ in place, on a branch
```

## Does Towel run my code?

No. It reads your code; it never imports or runs it. In a project that
configures them, it runs your type checker (mypy or Pyright) and formatter
(Ruff or Black) on the changed code, as you would. Towel itself uses no
network and no AI service.

## How do I undo a run?

Work on a Git branch, and `git restore` or `git checkout` undoes anything.
Without Git, write into a new directory (`towel dry src/ cleaned/`) and copy
back only what you want. If a run is interrupted while writing, `towel recover`
with the journal it names puts every file back
([recovery](CLI_GUIDE.md#recovery-and-interrupted-writes)).

## Why did Towel leave this code alone?

The run says, in a sentence per reason, and `--explain` lists every place.
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

Almost certainly. It assumes your program does not depend on exactly when
Python frees an object it no longer uses, that reading an attribute or
comparing has no side effects, and that module functions are not rebound
while a helper runs. When Towel sees code that may depend on the first, such
as a `__del__` method, the run says so.
[Relaxed and strict](RELAXED_AND_STRICT.md) has the details.

## Why are the helpers called `__extracted_func_0`?

Towel cannot know what a block means, only that it repeats.
`towel rename-helpers` renames helpers and their parameters in one step,
updating every call ([naming guide](NAMING.md)). You can write the names
yourself or ask an AI assistant to propose them.

## Why is my run slow?

Type checking each change is most of the time on a typed project. `--no-types`
is several times faster; `--time-limit` bounds any run; a single module takes
seconds. [Performance](PERFORMANCE.md) has typical times.

## Can I keep only some of the changes?

Yes, with Git: `git restore` a file, or `git add -p` to choose hunks
([using Towel on a project](USING_TOWEL.md#keep-some-changes-and-drop-others)).

## Can Towel fail a build when there is duplicated code?

`towel preview . --check` exits 3 when it would change code, and 0 when it
would not. There is a pre-commit hook too ([recipes](RECIPES.md)).

## Can I put my options in a file?

Yes, under `[tool.towel]` in `pyproject.toml`
([project settings](RECIPES.md#project-settings)).

## Which Python versions does Towel support?

Towel runs on Python 3.11 to 3.15, on macOS and Linux. It reads code with the
interpreter it runs on, so code it should read must parse on that version;
a file that does not is skipped with a warning. Windows is not supported.

## My script used `--summ` and now fails. Why?

Towel 2.0 accepts only full option names, so that a future option cannot change
what an abbreviation means. Use `--summary`
([changes in 2.0](https://github.com/ericeallen/towel/blob/main/CHANGELOG.md)).

## Where do I ask a question or report a bug?

See [getting help](GETTING_HELP.md).
