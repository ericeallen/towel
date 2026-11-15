# How Towel compares

[Documentation index](README.md)

Several tools deal with duplicated code. Most find it and leave the change to
you; Towel makes the change, and makes only changes it can show keep your
program's behaviour. Which you want depends on the job.

| Tool | Finds duplication | Changes your code | Checks the change |
| --- | --- | --- | --- |
| pylint's duplicate-code check | Yes, Python | No | — |
| jscpd | Yes, over 200 languages | No, though an optional skill asks an AI assistant to | — |
| PMD's CPD | Yes, many languages including Python | No | — |
| An IDE's extract-method | Only copies of the code you select | Yes, the code you select | No |
| An AI assistant | If you ask | Yes | No: you review and test it |
| Towel | Yes, Python | Yes, into shared helpers | Yes, before writing |

## Clone detectors: pylint, jscpd and PMD CPD

These report duplicated code and change nothing.

- **pylint** reports `duplicate-code` (R0801), "Similar lines in N files", and
  notes that this "usually means that the code should be refactored to avoid
  this duplication." By default it reports runs of at least 4 similar lines
  (`min-similarity-lines`) and leaves comments, docstrings, imports and function
  signatures out of the comparison
  ([checker features](https://pylint.readthedocs.io/en/stable/user_guide/checkers/features.html),
  [options](https://pylint.readthedocs.io/en/stable/user_guide/configuration/all-options.html)).
- **jscpd** is a "copy/paste detector for source code" covering over 220
  languages and formats. It tokenizes each file and finds repeated token
  sequences with a rolling Rabin-Karp hash; opt-in passes also find copies that
  differ in names or values, or by a few edited lines. It reports through
  fifteen reporters, among them console, HTML, JSON, SARIF and a threshold that
  fails a CI build. Its optional `dry-refactoring` skill hands the clones it
  finds to an AI coding assistant, which makes the change
  ([README](https://github.com/kucherenko/jscpd)).
- **PMD's Copy/Paste Detector (CPD)** finds duplicated code in many languages,
  Python among them. `--minimum-tokens` sets the shortest duplicate it reports,
  and it writes text, XML, CSV or Markdown reports
  ([CPD documentation](https://pmd.github.io/pmd/pmd_userdocs_cpd.html),
  [supported languages](https://pmd.github.io/pmd/tag_CpdCapableLanguage.html)).

**What they are good at:** they cover a whole codebase (jscpd and CPD across
many languages at once) and suit a CI gate that keeps duplication from growing. If you want a number, a report or a failing build, use one of
them; Towel can sit beside them to remove what they find in Python.

## An IDE's extract-method refactoring

In PyCharm you "select a block of code to be transformed into a method or a
function," name it and choose its parameters in a dialog; "if duplicate code
fragments are encountered, PyCharm suggests replacing them with the calls to the
extracted function"
([PyCharm: Extract method](https://www.jetbrains.com/help/pycharm/extract-method.html)).

**What it is good at:** you decide exactly what becomes a function and what it is
called, while you are editing. It starts from the code you select, one
extraction at a time, and looks only for copies of that code. Towel works the other way round: it searches the whole project
and proposes every extraction it can check, with placeholder names that you then
[replace](NAMING.md).

## An AI assistant

An assistant such as GitHub Copilot can propose a refactoring of any code you
show it, in any style you ask for. Its makers tell you to check the result:
"Understand suggested code before you implement it," "Review Copilot's
suggestions carefully," and "Use automated tests and tooling to check Copilot's
work"
([GitHub Copilot best practices](https://docs.github.com/en/copilot/get-started/best-practices)).

**What it is good at:** judgement about design and names, which Towel does not
attempt. What it does not give you is a check that the refactored program behaves
as before. Towel's changes are checked as they are made, so you can let an
assistant do what it is good at, [choosing names](NAMING.md) for the helpers
Towel wrote.

## What Towel does differently

Towel compares the syntax of every function in a Python project, finds blocks
that differ only in names, values or expressions, and turns each duplicated
block into a call to one shared helper. Before it writes anything it checks that
the change keeps what the program computes and does, including the order of its
side effects, and it runs the project's type checker on the result when one is
available. When it cannot show that the change is safe, it leaves the code
alone and, with `--explain`, says why. See
[Reading Towel's output](GENERATED_CODE.md) for what the result looks like.

Towel's limits are the other tools' strengths: it handles Python only; it
extracts helper functions and does not redesign your classes; it gives helpers
placeholder names that you replace; the largest projects take from several
minutes to over an hour rather than seconds (see [Performance](PERFORMANCE.md));
and its check covers your program's behaviour, not whether the new helper makes
the design better. Review the diff and run your tests, as the
[quick start](QUICKSTART.md) shows.

The other tools' descriptions above come from their own documentation as
read in November 2026: pylint 4.1, PMD 7, jscpd's README, PyCharm's help and
GitHub's Copilot documentation.
