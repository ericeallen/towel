# Why something was left alone

When Towel finds similar code and does not extract it, the run says why, in a
sentence, with the reason's name in brackets. Save this as `feed.py`:

```python
def read_orders(path):
    with open(path) as source:
        for line in source:
            if line.strip():
                yield line.split(",")


def read_refunds(path):
    with open(path) as source:
        for line in source:
            if line.strip():
                yield line.split(";")
```

```bash
towel preview feed.py
```

```text
Would apply 0 refactoring(s).
Analysed 1 Python file (2 functions).
Found 2 places with similar code and extracted none.

Why code was left alone (most frequent first; --explain locates each place):
  2  The block holds yield or yield from: as a helper of its own it would be a separate generator, and Towel does not share code that yields. [generator_block]

Places left alone, largest first:
  feed.py:2-5 and feed.py:9-12: The block holds yield or yield from: as a helper of its own it would be a separate generator, and Towel does not share code that yields.
  feed.py:3-5 and feed.py:10-12: The block holds yield or yield from: as a helper of its own it would be a separate generator, and Towel does not share code that yields.
Input files were not changed.
```

Each reason line gives how many places it applied to, its sentence and its
name. When there is something you can do, the next line says what. Below the
reasons, each place is named by file and lines, largest first; the run shows
the five largest, and `--explain` lists them all. A run that extracted something
leaves out the pairs it declined, because most are the calls it has just written;
`--explain` shows those too.

## When Towel finds nothing to do

A run that changes nothing ends in one of these ways.

**"No Python files found under PATH."** The target holds no Python file.
"No Python files selected for analysis under PATH" means every file was left out,
by default or by `--exclude`. "No Python file under PATH could be read" means
each one was skipped, and the warnings above it say why.

**"No repeated code found in N files."** Towel compares the bodies of functions and
methods, and found no two alike. It does not examine code outside functions:
statements at a module's top level, or a script's, are never extracted, and
neither is an early-return guard (`if not x: return`) on its own. When a file
holds such code, the run says "No repeated code found in the code Towel
examined in N files." A single line counts the files that hold such code.
With `--explain`, any run lists the affected files and the number of statements
or guards in each. JSON includes these counts in `analysed.not_examined`.
To have Towel compare module-level statements, move them into a function.
For guards, extract the shared condition into a function and keep each
`return` in its caller.

**"Found N places with similar code and extracted none."** Towel found code of
the same shape and the reasons above say why it left each place alone. Similar
is not the same as duplicated: most of these places are short, or differ in
more than values, and read better as they are. If the reasons are that a
helper would not be smaller, or that the copies differ too much, Towel is
telling you there is nothing worth extracting, and you are done. Do not
rewrite code only so that Towel can extract it. Act on a reason when its
place is code you would have extracted by hand; the sentence's "What you can
do" line, where there is one, says how.

`--explain` lists every place the final analysis left alone, with its reason,
which helps you find the one you meant; on a large project it is long.

## The reasons you will meet most

- **The helper would not be smaller** (`unprofitable_helper`). A helper and its
  calls must be smaller than the copies they replace. Short duplicates often
  fail this, and are better left as they are.
- **The copies differ too much** (`not_structurally_similar`,
  `unification_failed`). A parameter can stand for a value, a variable or an
  expression, including text inside an f-string, but not for an operator, and
  each difference needs a parameter: more than `--max-parameters` (5) and the
  copies stay. Raise `--max-parameters` only when the sentence says the
  copies need more parameters.
- **A variable carries over** (`unsafe_reassignment_block1`,
  `conditionally_bound_return`). A block that continues a variable set before
  it (`total += x` after `total = 0`), or sets one only on some paths, cannot
  hand it back cleanly. A larger block that includes the first assignment
  often qualifies; Towel tries those too.
- **The code yields, or escapes** (`generator_block`, `created_object_escapes`,
  `frame_sensitive_block`). A generator, a lambda handed to code Towel cannot
  see, or a `break` for a loop outside the block only works where it is.
- **A resource could close early** (`resource_lifetime_unproven`). The block
  opens a file, socket or temporary directory that later code may still need.
  Use a `with` statement, or `--strict`.
- **The type checker objected** (`refused by the type checker` and the other
  typing reasons). In a typed project every change must type-check. `--no-types`
  extracts without checking ([typed projects](TYPING.md)).
- **Only with `--strict`** (`owned_binding_frame_boundary`,
  `ownership_parameter_budget`, `lifetime_order_uncertified`). These keep the
  exact moment Python releases objects; the default, relaxed mode, does not
  need them ([relaxed and strict](RELAXED_AND_STRICT.md)). `truth_test_repeated`
  is also strict mode's alone: it keeps a value's truth from being tested twice.
- **A resource inside `try` or `with`** (`owned_binding_exception_boundary`).
  Strict mode gives this reason for any variable the block assigns there. A
  relaxed run gives it only for a resource Towel protects, because a helper
  that raised could not hand the resource back.
- **Only with `--cross-module`.** Sentences that start "Only with --cross-module"
  concern a helper shared between modules ([sharing across modules](CROSS_MODULE.md)).

## Leaving code alone on purpose

To keep Towel away from a function, end its `def` line with
`# towel: no-extract`; to skip a file or directory, pass `--exclude NAME`
([command reference](CLI_GUIDE.md#protect-a-function)). Neither is ever
needed for safety in ordinary code: they are for code that reads its own
source or frames, or that a tool rewrites.

## Every reason

The sentences are exactly what a run prints. A reason counted for a repeated
expression, rather than a block, is printed with a sentence about an
expression. With `--json`, the result's `left_alone` field counts the places
the run lists by default ([automation](COMPATIBILITY.md)).

<!-- BEGIN reason table: generated by tests/test_why_left_alone.py -->

### When Towel compares two places

| Reason | What it means | What you can do |
| --- | --- | --- |
| `bare_name_differs_by_module` | Only with --cross-module: a helper shared between modules would read a module-level name that means something different in each of them. |  |
| `builtin_argument` | The call would have to pass a builtin such as len to the helper, or one block uses a variable named like a builtin that the other uses as the builtin. | Rename the variable that shadows the builtin. |
| `builtin_mapping_replaced` | Code in the project assigns or deletes __builtins__, so code moved into a helper could see different builtins. | Remove that code, or leave its directory out with --exclude. |
| `builtin_may_differ_by_module` | Only with --cross-module: the shared helper would look up a builtin such as len in the module holding the helper, not in the module the code came from. | Pass --parameterize-builtins to hand such builtins to the helper as arguments. |
| `closure_crosses_block_boundary` | A nested function, lambda or comprehension and the block use a variable of the same name that one of them reassigns, so moving the block could change which variable the closure sees. | Rename the lambda parameter or comprehension variable if it only shares the name. |
| `conditionally_bound_return` | A variable the helper would hand back is not assigned on every path through the block, for example only inside a loop or an if. | Assign it on every path inside the block. |
| `conditionally_imported_host` | Only with --cross-module: the project imports the module that would hold the helper only under a condition. |  |
| `coroutine_exception_boundary` | The block awaits, so it would become an async helper, and an exception would leave that helper changed: a StopIteration turns into a RuntimeError before a handler or with statement around the block, or in its async generator, sees it, or a GeneratorExit the block stops is raised again in its function. |  |
| `created_object_escapes` | The block creates a function, lambda, class or generator that Towel cannot prove safe to move into a helper. | Use sorted, min or max with key=, or define the function at module level. |
| `cross_module_global_declaration` | Only with --cross-module: one of the modules involved has a function that declares a name global. |  |
| `directive_around_block` | A tool comment outside the block (the line above, the enclosing def or if line, an open # fmt: off) covers the block and would not cover the helper. | Remove or move the comment. |
| `directive_on_argument` | A tool comment covers an expression that would move to the call as an argument, where the comment would no longer cover it. | Remove the comment, or make the lines identical. |
| `directive_on_shared_line` | The block shares a line that carries a tool comment with code that stays (a = 1; b = 2  # noqa). | Put the statements on separate lines. |
| `directive_outlives_block` | A region or file-wide tool comment that starts in the block also covers code after it. | Close the region inside the block. |
| `directives_differ` | The blocks carry different tool comments (# type: ignore, # noqa, # pragma: no cover) on matching lines. | Make the comments the same. |
| `duplicate_proposal` | Another pair of these blocks already proposed the same extraction; nothing was lost. |  |
| `excluded_block_start` | The block starts with a statement excluded from coverage; the call replacing it would be measured and never run. |  |
| `existing_helper_becomes_forwarder` | The extraction would turn a helper made earlier in this run into a one-line call of another helper. |  |
| `forwarded_callee` | Passing the different callees would require a forwarding lambda that repeats their lookup on each call. | Assign the method to a variable first, then call the variable. |
| `frame_read_in_function` | The block reads super or __class__, which gives its function the class cell that a call of super through another name, elsewhere in the function, needs. |  |
| `frame_sensitive_block` | The block only works in its own function: it holds a break or continue for a loop outside it, an assignment expression in a comprehension, a super() call that needs its own method, or a return from an async generator. |  |
| `generator_block` | The block holds yield or yield from: as a helper of its own it would be a separate generator, and Towel does not share code that yields. |  |
| `host_has_stub` | Only with --cross-module: the module that would hold the helper has a .pyi stub, which type checkers read in its place and which would not list the helper. |  |
| `import_cycle` | Only with --cross-module: whichever module held the helper, the new import would be circular. |  |
| `import_time_effects` | Only with --cross-module: the new import could run module-level code, load a module the package had not loaded, or change the order modules load in. | Sharing works when the module that would borrow the helper already imports the module holding it, unconditionally, at its top. |
| `impure_eager_parameter` | Towel cannot pass a differing expression at the call without changing when it is evaluated or whether it can fail. |  |
| `incomplete_lifetime_block1` | The first block reads a variable that is assigned only later in its function, or before the block's own assignment to it. |  |
| `incomplete_lifetime_block2` | The second block reads a variable that is assigned only later in its function, or before the block's own assignment to it. |  |
| `incomplete_return_coverage_block1` | The first block returns on some paths and carries on on others; early-return guards are not extracted. |  |
| `incomplete_return_coverage_block2` | The second block returns on some paths and carries on on others; early-return guards are not extracted. |  |
| `instantiation_mismatch` | Towel's own check failed: putting a call's arguments back into the helper did not give back the original code. |  |
| `layout_not_kept` | A # fmt: skip or # fmt: off comment requires lines of the block to stay exactly as written, and the helper cannot keep them. | Remove the formatter comment. |
| `lifetime_order_uncertified` | Strict mode cannot yet preserve the order in which objects are released on this Python runtime, so these blocks were left unchanged. |  |
| `literal_pool_boundary` | The block holds constants whose identity (what `is` compares) could change if the code were compiled in another function or module, such as arithmetic on float literals or a one-byte bytes literal. |  |
| `mixed_return_and_variables` | The block both returns from its function and assigns variables that later code reads. |  |
| `module_data_lookup` | The block reads a module-level variable (data, not a function, class or import) that would have to be passed as an argument, fixing its value at the call. |  |
| `moves_only_binding` | The block holds its function's only assignment to a name that other code in the function reads, and the call would not assign it. |  |
| `moves_scope_declaration` | The block holds a global or nonlocal statement that code outside the block depends on. |  |
| `narrowing_lost_at_call_site` | The block holds a type test (isinstance, is None) that code left behind, or an argument, relies on; inside a helper the type checker would not see it. |  |
| `needs_class_body` | The block calls super() without arguments, and the helper cannot be a method of that class. |  |
| `nested_binding_escapes` | The block is inside a loop or branch and assigns a variable that code elsewhere in the function reads. |  |
| `new_import_requirement` | Only with --cross-module: importing the module holding the helper would make the other module need a package it does not need today. |  |
| `new_top_level_package` | Only with --cross-module: the helper would live in a top-level package the other module never imports. |  |
| `nonlocal_safety_skip` | The block reads, assigns or declares a nonlocal variable. |  |
| `not_structurally_similar` | The blocks differ too much in size or structure to share a helper. |  |
| `orphaned_variables` | Later code would read a variable that only the block assigns and the helper does not hand back. |  |
| `other` | The pair was declined without a recorded reason. |  |
| `other_distribution` | Only with --cross-module: the two modules ship in different installable distributions. |  |
| `owned_binding_exception_boundary` | The block is inside a try or with statement and assigns a variable; if the helper raised, that variable's object would be released earlier than it is now. |  |
| `owned_binding_frame_boundary` | Moving the block would change the order in which its function releases objects when it returns: the function's arguments or other variables on one side, variables the block assigns on the other. |  |
| `ownership_parameter_budget` | Keeping the function's arguments alive exactly as long as now needs one more hidden parameter, which would take the helper past --max-parameters. | Raise --max-parameters. |
| `private_name_lexical_class` | The block uses a double-underscore private name (self.__x), and the helper would live outside that class. |  |
| `rebound_external_binding` | The block reads a variable that another function can reassign (through global, nonlocal or a closure) while the block runs. |  |
| `relative_import_across_packages` | Only with --cross-module: the block holds a relative import that would name a different module from the module holding the helper. | Use an absolute import. |
| `repeated_statement_group` | This helper would bundle independent repetitions of the same operation. |  |
| `resource_lifetime_unproven` | This block creates or weakly references a resource that later code may depend on, and Towel cannot keep it alive in a helper. | Use a with statement to manage the resource, or use --strict. |
| `return_variables_not_aligned` | The variables each block would hand back do not correspond one to one, or handing them back would change the order their objects are released in. |  |
| `return_versus_variables` | One block ends by returning; the other is followed by code that reads its variables. |  |
| `run_by_path_import` | Only with --cross-module: one module is run as a script, where the new import would not resolve. |  |
| `scope_declarations_differ` | One function declares a name global or nonlocal and the other does not. |  |
| `super_in_call` | The call replacing the block would have to evaluate super() inside a lambda, where it does not work. |  |
| `thunk_of_possibly_unbound_local` | An argument would be wrapped in a lambda and reads a local variable that may have no value yet; the error raised then would change from UnboundLocalError to NameError. | Assign the variable on every path before the block. |
| `trivial_forwarding_helper` | The helper would only forward calls, raise, rename or unpack values, or call earlier helpers; it would share no new logic. |  |
| `trivial_return_blocks` | The helper would compute nothing: it would return what it was given. |  |
| `truth_test_repeated` | With --strict: the expression decides an if or while at one place and gives a value at another, and one helper cannot serve both: returning the value would test a value's truth twice at the first place. | Without --strict the expression is extracted. |
| `unbinds_external_name` | The block deletes (with del or except ... as) a variable assigned before it. |  |
| `undefined_names_in_call` | The call replacing the block would use a name that does not exist where the call stands. |  |
| `unification_failed` | The blocks differ in something a parameter cannot stand for, such as an operator or variable bindings that do not correspond, or need more parameters than --max-parameters allows. | If the blocks need more parameters, raise --max-parameters. |
| `unprofitable_helper` | The shared code does not repay the helper: the helper and the calls that replace the blocks would not be smaller than the blocks themselves (counted in syntax, not lines). |  |
| `unproven_import` | Only with --cross-module: no existing import in the project shows how one of the modules can import the other. | See 'Resolving import refusals' in docs/CROSS_MODULE.md. |
| `unsafe_reassignment_block1` | The first block reassigns a variable first assigned before it, such as total += x with total = 0 above. |  |
| `unsafe_reassignment_block2` | The second block reassigns a variable first assigned before it, such as total += x with total = 0 above. |  |
| `unsupported_extraction` | Towel could not write the helper or one of its calls. |  |
| `unsupported_syntax` | These blocks contain syntax whose evaluation Towel cannot yet preserve; they were left unchanged. |  |
| `value_producing_mismatch` | One block produces a value (it returns, or later code reads its variables) and the other does not. |  |

### When Towel checks a change it has made

| Reason | What it means | What you can do |
| --- | --- | --- |
| `changed nothing` | Applying it would have left the code exactly as it was. |  |
| `completes its caller's partial type` | The type checker learns an empty collection's element type from the code that would move into the helper. | To proceed without type verification, use --no-types. |
| `could not be rendered` | Towel could not write the helper or one of its calls. |  |
| `declares its class's attributes` | The block's assignments through self declare its class's attributes, and a helper outside the class would declare none. | To proceed without type verification, use --no-types. |
| `narrows what a call-site lambda reads` | A type test that one argument relies on would be outside the lambda passing it, where the type checker cannot see it. | To proceed without type verification, use --no-types. |
| `narrows what its caller reads after the call` | The block tests a type (isinstance, is None) that code after it relies on, and the type checker would not carry that test out of a helper. | To proceed without type verification, use --no-types. |
| `not judged: the type checker could not run` | The type checker could not run for this change, so it was not judged (see the warnings above). | To proceed without type verification, use --no-types. |
| `not representable in its file's encoding` | The changed file would hold a character its declared encoding cannot represent. |  |
| `not verifiable: its file holds a name the type checker cannot type` | The type checker could not verify the proposed refactoring(s), so those blocks were left unchanged. | To proceed without type verification, use --no-types; for a typed run, install the project's dependencies where Towel runs (including their type stubs). |
| `not verifiable: the type checker does not look at the code it changes` | Towel could not establish that the type checker examines the changed code, so the change could not be verified. | To proceed without type verification, use --no-types. |
| `not writable in place: its file is hard-linked` | Its file has more than one hard link, and an in-place run would leave the other links holding the old text. | Write the result to a new output, or give the file a single link. |
| `refused by the type checker` | The project's type checker reported a new error with the helper in place. | To proceed without type verification, use --no-types. |
| `repeated_statement_group` | This helper would bundle independent repetitions of the same operation. |  |
| `the configured Ruff check failed or introduced diagnostics` | The project's Ruff check failed on the changed file, or reported something new there. |  |
| `the formatter changed its code or failed` | The project's formatter failed on the changed code, or changed more than its layout. | Pass --no-format to leave the generated code unformatted. |
| `unprofitable helper` | The shared code does not repay the helper: the helper and the calls that replace the blocks would not be smaller than the blocks themselves (counted in syntax, not lines). |  |
| `would be the one unannotated function of its module` | No annotated signature of the helper type-checks, and an unannotated one would be the only unannotated function of a fully annotated module. | To proceed without type verification, use --no-types. |

<!-- END reason table -->

**Next:** [using Towel on a project](USING_TOWEL.md) takes a change from branch to commit.

[Documentation index](README.md)
