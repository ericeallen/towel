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

"""CI strict policy must reach inference, baseline, candidate and cold validation alike."""

from pathlib import Path
import textwrap

import pytest

from towel.mypy_ci_policy import UnsupportedMypyPolicy, mypy_policy
from towel.type_inference import CheckFailure, CheckSuccess, MypyInferrer, type_oracle_for_project
from towel.unification.annotation_wiring import mypy_ladder_flags


def _project(root: Path, command: str, *, step: str = "", job: str = "") -> Path:
    package = root / "pkg"
    package.mkdir(exist_ok=True)
    (package / "__init__.py").write_text("")
    module = package / "m.py"
    module.write_text("def value() -> int:\n    return 1\n")
    (root / "pyproject.toml").write_text("[project]\nname = 'fixture'\n")
    workflows = root / ".github/workflows"
    workflows.mkdir(parents=True, exist_ok=True)
    (workflows / "ci.yml").write_text(
        "jobs:\n  lint:\n" + job + "    steps:\n      - run: " + command + "\n" + step
    )
    return module


@pytest.mark.parametrize(
    "command",
    [
        "mypy --strict pkg",
        "python -m mypy --strict pkg",
        "python3.13 -m mypy --strict pkg",
        "uv run --frozen mypy --strict pkg",
        "'mypy --strict pkg'",
        '"mypy --strict pkg"',
        "|\n          mypy --strict pkg",
    ],
)
def test_literal_policy_has_actual_path_scope_and_provenance(tmp_path: Path, command: str) -> None:
    module = _project(tmp_path, command)
    policy = mypy_policy(tmp_path, [module])
    assert policy.flags == ("--strict",)
    assert policy.provenance == (".github/workflows/ci.yml job lint step 1 line 4",)
    outside = tmp_path / "unmarked.py"
    outside.write_text("pass\n")
    assert mypy_policy(tmp_path, [outside]).flags == ()
    with pytest.raises(UnsupportedMypyPolicy, match="spans strict CI targets"):
        mypy_policy(tmp_path, [module, outside])


@pytest.mark.parametrize(
    "command,step,job",
    [
        ("mypy --strict $PACKAGE", "", ""),
        ("mypy --strict pkg --platform linux", "", ""),
        ("mypy --strict pkg && echo done", "", ""),
        ("|\n          if true; then\n            mypy --strict pkg\n          fi", "", ""),
        ("mypy --strict pkg", "        if: ${{ matrix.check }}\n", ""),
        ("mypy --strict pkg", "", "    if: false\n"),
        ("mypy --strict pkg", "        working-directory: pkg\n", ""),
        ("mypy --strict pkg", "        shell: pwsh\n", ""),
        ("mypy --strict pkg", "        env:\n          CHECK: strict\n", ""),
        ("mypy --strict pkg", "", "    defaults:\n      run:\n        working-directory: pkg\n"),
        ("mypy --strict", "", ""),
        ("mypy --strict missing", "", ""),
    ],
)
def test_unknown_declared_policy_refuses_instead_of_weaker_defaults(
    tmp_path: Path, command: str, step: str, job: str
) -> None:
    module = _project(tmp_path, command, step=step, job=job)
    with pytest.raises(UnsupportedMypyPolicy):
        mypy_policy(tmp_path, [module])
    oracle = MypyInferrer()
    try:
        assert isinstance(oracle.check_project({str(module): module.read_text()}), CheckFailure)
    finally:
        oracle.close()


def test_overlapping_different_commands_are_ambiguous(tmp_path: Path) -> None:
    module = _project(tmp_path, "|\n          mypy --strict pkg\n          mypy pkg")
    with pytest.raises(UnsupportedMypyPolicy, match="conflicting"):
        mypy_policy(tmp_path, [module])


@pytest.mark.parametrize(
    "command", ["echo 'mypy --strict pkg'", "python -m pip install mypy", "mypy pkg"]
)
def test_installation_documentation_and_default_run_do_not_force_strict(
    tmp_path: Path, command: str
) -> None:
    module = _project(tmp_path, command)
    assert mypy_policy(tmp_path, [module]).flags == ()


def test_cli_strict_overrides_global_flags_but_preserves_per_module_overrides(
    tmp_path: Path,
) -> None:
    module = _project(tmp_path, "mypy --strict pkg")
    (tmp_path / "pyproject.toml").write_text(
        "[tool.mypy]\nstrict = false\nwarn_return_any = false\n"
        "[[tool.mypy.overrides]]\nmodule = 'pkg.loose'\nwarn_return_any = false\n"
    )
    loose = module.with_name("loose.py")
    loose.write_text("")
    assert mypy_ladder_flags(module) == {
        "disallow_untyped_defs": True,
        "warn_return_any": True,
        "check_untyped_defs": True,
    }
    assert mypy_ladder_flags(loose)["warn_return_any"] is False
    from towel import _mypy_worker as worker

    options = worker._options(
        tmp_path,
        str(tmp_path / "pyproject.toml"),
        str(tmp_path / ".cache"),
        probe=False,
        cli_flags=("--strict",),
    ).options
    assert options.warn_return_any and options.disallow_untyped_defs
    assert options.clone_for_module("pkg.loose").warn_return_any is False


def test_strict_any_return_regression_is_rejected_by_warm_and_cold_project_checks(
    tmp_path: Path,
) -> None:
    module = _project(tmp_path, "mypy --strict pkg")
    original = textwrap.dedent("""
        from typing import Any
        def helper(value: Any) -> Any:
            return value
        def typed(value: int) -> int:
            return value
    """).lstrip()
    changed = original.replace(
        "def typed(value: int) -> int:\n    return value",
        "def typed(value: int) -> int:\n    return helper(value)",
    )
    module.write_text(original)
    choice = type_oracle_for_project(module)
    oracle = choice.tool
    assert isinstance(oracle, MypyInferrer)
    try:
        oracle.begin_run([str(module)])
        baseline = oracle.check_project({str(module): original})
        assert isinstance(baseline, CheckSuccess) and not baseline.errors
        for cold in (False, True):
            if cold:
                oracle.forget_warm_state()
            verdict = oracle.check_project({str(module): changed})
            assert isinstance(verdict, CheckSuccess)
            assert len(verdict.errors) == 1
            assert (
                'Returning Any from function declared to return "int"' in verdict.errors[0].message
            )
    finally:
        oracle.close()


def test_unrelated_valid_extraction_remains_useful_under_ci_strict(tmp_path: Path) -> None:
    from towel.unification.refactor_engine import UnificationRefactorEngine

    module = _project(tmp_path, "mypy --strict pkg")
    source = textwrap.dedent("""
        def first(value: int) -> int:
            scaled = value * 2
            label = str(scaled)
            return len(label) + scaled

        def second(value: int) -> int:
            scaled = value * 3
            label = str(scaled)
            return len(label) + scaled
    """).lstrip()
    module.write_text(source)
    oracle = MypyInferrer()
    try:
        engine = UnificationRefactorEngine(min_lines=3, type_oracle=oracle)
        proposals = engine.analyze_files([str(module)], progress="none")
        assert proposals
        changed = engine.apply_refactoring(str(module), proposals[0])
        assert changed != source and "extracted_func" in changed
        verdict = oracle.check_project({str(module): changed})
        assert isinstance(verdict, CheckSuccess) and not verdict.errors
        namespace: dict[str, object] = {}
        exec(
            compile(
                changed + "\nassert first(4) == 9\nassert second(4) == 14\n", str(module), "exec"
            ),
            namespace,
        )
        assert module.read_text() == source
    finally:
        oracle.close()


def test_false_positive_names_comments_and_installs_do_not_select_another_checker(
    tmp_path: Path,
) -> None:
    from towel.mypy_ci_policy import declared_mypy_root

    module = _project(tmp_path, "echo 'mypy --strict pkg'")
    assert declared_mypy_root(module) is None
    workflow = tmp_path / ".github/workflows/ci.yml"
    workflow.write_text(
        "# mypy --strict pkg\njobs:\n  lint:\n    steps:\n      - name: install mypy\n        run: python -m pip install mypy\n"
    )
    assert declared_mypy_root(module) is None


def test_workflow_ambiguity_and_target_escape_cannot_read_as_default_success(
    tmp_path: Path,
) -> None:
    module = _project(tmp_path, "mypy --strict ../")
    with pytest.raises(UnsupportedMypyPolicy, match="project path"):
        mypy_policy(tmp_path, [module])
    workflow = tmp_path / ".github/workflows/ci.yml"
    workflow.write_text(
        "jobs:\n  lint:\n    steps:\n      - run: mypy --strict pkg\n        run: mypy pkg\n"
    )
    with pytest.raises(UnsupportedMypyPolicy, match="ambiguous"):
        mypy_policy(tmp_path, [module])


def test_mixed_strict_targets_fail_explicitly_at_the_real_worker_boundary(tmp_path: Path) -> None:
    module = _project(tmp_path, "mypy --strict pkg")
    other = tmp_path / "outside.py"
    other.write_text("def f() -> int:\n    return 1\n")
    oracle = MypyInferrer()
    try:
        oracle.begin_run([str(module), str(other)])
        verdict = oracle.check_project(
            {str(module): module.read_text(), str(other): other.read_text()}
        )
        assert isinstance(verdict, CheckFailure) and "narrow the input" in verdict.reason
    finally:
        oracle.close()


@pytest.mark.parametrize(
    "script",
    [
        "python -m pip install mypy\n          mypy --strict pkg",
        "pip install mypy\n          python -m mypy --strict pkg",
        "uv pip install mypy\n          mypy --strict pkg",
    ],
)
def test_install_prefix_does_not_hide_a_later_strict_invocation(
    tmp_path: Path, script: str
) -> None:
    module = _project(tmp_path, "|\n          " + script)
    assert mypy_policy(tmp_path, [module]).flags == ("--strict",)


@pytest.mark.parametrize(
    "extra", ["env:\n  MYPYPATH: stubs\n", "defaults:\n  run:\n    working-directory: pkg\n"]
)
def test_workflow_execution_context_is_not_silently_ignored(tmp_path: Path, extra: str) -> None:
    module = _project(tmp_path, "mypy --strict pkg")
    path = tmp_path / ".github/workflows/ci.yml"
    path.write_text(extra + path.read_text())
    with pytest.raises(UnsupportedMypyPolicy, match="custom directory/environment/shell"):
        mypy_policy(tmp_path, [module])


@pytest.mark.parametrize("run", [">\n          mypy --strict pkg"])
def test_computed_folded_or_aliased_named_check_refuses(tmp_path: Path, run: str) -> None:
    module = _project(tmp_path, run)
    path = tmp_path / ".github/workflows/ci.yml"
    path.write_text(path.read_text().replace("- run:", "- name: mypy --strict\n        run:"))
    with pytest.raises(UnsupportedMypyPolicy):
        mypy_policy(tmp_path, [module])


@pytest.mark.parametrize(
    "configured", ["files = ['other']", "packages = ['other']", "modules = ['other.m']"]
)
def test_literal_cli_targets_override_configured_targets_in_warm_and_cold_checks(
    tmp_path: Path, configured: str
) -> None:
    module = _project(tmp_path, "mypy --strict pkg")
    other = tmp_path / "other"
    other.mkdir()
    (other / "__init__.py").write_text("")
    (other / "m.py").write_text("pass\n")
    (tmp_path / "pyproject.toml").write_text("[tool.mypy]\n" + configured + "\n")
    original = "from typing import Any\ndef helper(value: Any) -> Any:\n    return value\ndef typed(value: int) -> int:\n    return value\n"
    changed = original.replace(
        "def typed(value: int) -> int:\n    return value",
        "def typed(value: int) -> int:\n    return helper(value)",
    )
    module.write_text(original)
    oracle = MypyInferrer()
    try:
        oracle.begin_run([str(module)])
        assert oracle.check_project({str(module): original}) == CheckSuccess()
        for cold in (False, True):
            if cold:
                oracle.forget_warm_state()
            verdict = oracle.check_project({str(module): changed})
            assert isinstance(verdict, CheckSuccess) and len(verdict.errors) == 1
            assert "Returning Any" in verdict.errors[0].message
            assert str(module.resolve()) in oracle.reports_on([str(module)])
    finally:
        oracle.close()


@pytest.mark.parametrize(
    "command",
    [
        "mypy --platform linux pkg",
        "mypy --python-version 3.10 pkg",
        "mypy --config-file custom.ini pkg",
        "mypy --install-types --non-interactive pkg",
        "python -m mypy --strict-equality pkg",
    ],
)
def test_non_strict_unknown_cli_flags_preserve_configured_or_default_checking(
    tmp_path: Path, command: str
) -> None:
    module = _project(tmp_path, command)
    (tmp_path / "pyproject.toml").write_text("[tool.mypy]\nwarn_return_any = true\n")
    assert mypy_policy(tmp_path, [module]).flags == ()
    from towel.mypy_ci_policy import declared_mypy_root

    assert declared_mypy_root(module) is None
    oracle = MypyInferrer()
    try:
        assert oracle.check_project({str(module): module.read_text()}) == CheckSuccess()
    finally:
        oracle.close()


@pytest.mark.parametrize("indicator", ["|2", "|2-", ">2+"])
def test_unsupported_yaml_indentation_indicators_cannot_hide_a_strict_check(
    tmp_path: Path, indicator: str
) -> None:
    module = _project(tmp_path, indicator + "\n          mypy --strict pkg")
    with pytest.raises(UnsupportedMypyPolicy):
        mypy_policy(tmp_path, [module])


@pytest.mark.parametrize(
    "patterns",
    [
        [("pkg.m", True), ("pkg.*", False)],
        [("pkg.*", False), ("pkg.m", True)],
        [("pkg.*.m", True), ("pkg.*", False)],
        [("pkg.*", False), ("pkg.*.m", True)],
        [("pkg.m.*", True), ("pkg.*", False)],
        [("pkg.*", False), ("pkg.m.*", True)],
        [("pkg.*.m", False), ("*.m", True)],
        [("*.m", True), ("pkg.*.m", False)],
    ],
)
def test_ladder_per_module_precedence_matches_native_mypy(
    tmp_path: Path, patterns: list[tuple[str, bool]]
) -> None:
    from towel import _mypy_worker as worker

    module = _project(tmp_path, "mypy --strict pkg")
    config = tmp_path / "pyproject.toml"
    config.write_text(
        "[tool.mypy]\n"
        + "".join(
            "[[tool.mypy.overrides]]\nmodule = "
            + repr(pattern)
            + "\nwarn_return_any = "
            + str(flag).lower()
            + "\n"
            for pattern, flag in patterns
        )
    )
    native = worker._read_configuration(str(config), ("--strict",)).options.clone_for_module(
        "pkg.m"
    )
    assert mypy_ladder_flags(module)["warn_return_any"] == native.warn_return_any


def test_non_strict_computed_ci_declaration_is_outside_the_new_contract(tmp_path: Path) -> None:
    module = _project(tmp_path, "${{ matrix.command }}", step="        name: mypy\n")
    assert mypy_policy(tmp_path, [module]).flags == ()


def test_strict_ci_declaration_does_not_select_mypy_for_an_unrelated_file(tmp_path: Path) -> None:
    from towel.mypy_ci_policy import declared_mypy_root

    module = _project(tmp_path, "mypy --strict pkg")
    outside = tmp_path / "outside.py"
    outside.write_text("pass\n")
    assert declared_mypy_root(module) == tmp_path
    assert declared_mypy_root(tmp_path) == tmp_path
    assert declared_mypy_root(outside) is None


@pytest.mark.parametrize("run", ["${{ matrix.command }}", "*check"])
def test_labels_alone_do_not_establish_a_strict_checker_declaration(
    tmp_path: Path, run: str
) -> None:
    module = _project(tmp_path, run, step="        name: mypy --strict\n")
    assert mypy_policy(tmp_path, [module]).flags == ()


@pytest.mark.parametrize(
    "checker,flag",
    [
        (r"my\u0070y", r"\u002d\u002dstrict"),
        ("mypy", r"\x2d\x2dstrict"),
        (r"my\U00000070y", r"\U0000002d\U0000002dstrict"),
    ],
)
def test_decoded_checker_and_strict_tokens_reach_the_real_checker(
    tmp_path: Path, checker: str, flag: str
) -> None:
    module = _project(tmp_path, f'"{checker} {flag} pkg"')
    assert mypy_policy(tmp_path, [module]).flags == ("--strict",)
    source = "from typing import Any\ndef helper(v: Any) -> Any:\n    return v\ndef typed(v: int) -> int:\n    return helper(v)\n"
    module.write_text(source)
    oracle = MypyInferrer()
    try:
        verdict = oracle.check_project({str(module): source})
        assert isinstance(verdict, CheckSuccess) and len(verdict.errors) == 1
        assert "Returning Any" in verdict.errors[0].message
    finally:
        oracle.close()


@pytest.mark.parametrize("shape", ["ordinary", "flow", "anchor", "env"])
def test_decoded_strict_tokens_in_unsupported_declarations_never_become_defaults(
    tmp_path: Path, shape: str
) -> None:
    command = r'"my\u0070y \u002d\u002dstrict $PACKAGE"'
    module = _project(tmp_path, command)
    workflow = tmp_path / ".github/workflows/ci.yml"
    if shape == "flow":
        workflow.write_text("jobs: {lint: {steps: [{run: " + command + "}]}}\n")
    elif shape == "anchor":
        workflow.write_text(workflow.read_text().replace("run: ", "run: &check "))
    elif shape == "env":
        workflow.write_text("env:\n  PACKAGE: pkg\n" + workflow.read_text())
    with pytest.raises(UnsupportedMypyPolicy):
        mypy_policy(tmp_path, [module])
    oracle = MypyInferrer()
    try:
        assert isinstance(oracle.check_project({str(module): module.read_text()}), CheckFailure)
    finally:
        oracle.close()


@pytest.mark.parametrize(
    "command",
    [
        r'"echo my\u0070y \u002d\u002dstrict pkg"',
        "'echo mypy --strict pkg'",
        "mypy '--strict' pkg",
        "'mypy ''--strict'' pkg'",
    ],
)
def test_shell_quoting_is_respected_and_echoed_examples_are_not_policy(
    tmp_path: Path, command: str
) -> None:
    module = _project(tmp_path, command)
    assert bool(mypy_policy(tmp_path, [module]).flags) == ("echo" not in command)


def test_quoted_run_key_in_unsupported_yaml_still_refuses_a_literal_strict_check(
    tmp_path: Path,
) -> None:
    module = _project(tmp_path, r'"my\u0070y \x2d\x2dstrict pkg"')
    path = tmp_path / ".github/workflows/ci.yml"
    path.write_text(path.read_text().replace("run:", '"run":'))
    with pytest.raises(UnsupportedMypyPolicy):
        mypy_policy(tmp_path, [module])


@pytest.mark.parametrize(
    "run",
    [
        '"mypy\n          --strict pkg"',
        "'mypy\n          --strict pkg'",
        "\n          mypy --strict pkg",
        "mypy\n          --strict pkg",
        r'"my\u0070y' + "\n          " + r'\u002d\u002dstrict pkg"',
    ],
)
def test_split_or_continued_run_headers_never_hide_strict_policy(tmp_path: Path, run: str) -> None:
    module = _project(tmp_path, run)
    with pytest.raises(UnsupportedMypyPolicy):
        mypy_policy(tmp_path, [module])


def test_comments_names_and_echoed_quoted_check_examples_are_not_policy(tmp_path: Path) -> None:
    module = _project(
        tmp_path, '"echo \\"mypy --strict pkg\\""', step="        name: mypy --strict\n"
    )
    path = tmp_path / ".github/workflows/ci.yml"
    path.write_text("# mypy --strict pkg\n" + path.read_text())
    assert mypy_policy(tmp_path, [module]).flags == ()


@pytest.mark.parametrize(
    "command",
    [
        "echo setup; mypy --strict pkg",
        "echo setup && mypy --strict pkg",
        "echo setup || mypy --strict pkg",
        "echo setup | mypy --strict pkg",
        "printf setup; python -m mypy --strict pkg",
        "echo $(mypy --strict pkg)",
        "echo `mypy --strict pkg`",
        "echo setup > log; mypy --strict pkg",
        "echo setup\\\n          mypy --strict pkg",
    ],
)
def test_echo_or_printf_cannot_hide_a_compound_strict_declaration(
    tmp_path: Path, command: str
) -> None:
    module = _project(tmp_path, "|\n          " + command)
    with pytest.raises(UnsupportedMypyPolicy):
        mypy_policy(tmp_path, [module])
    oracle = MypyInferrer()
    try:
        assert isinstance(oracle.check_project({str(module): module.read_text()}), CheckFailure)
    finally:
        oracle.close()


@pytest.mark.parametrize(
    "command",
    [
        "echo 'mypy --strict pkg; example'",
        "echo 'mypy --strict $PACKAGE'",
        "printf 'mypy --strict pkg | example'",
        "echo 'mypy --strict pkg && example'",
        "echo 'mypy --strict pkg > example'",
    ],
)
def test_quoted_literal_shell_operators_in_echo_examples_are_not_checker_policy(
    tmp_path: Path, command: str
) -> None:
    module = _project(tmp_path, "|\n          " + command)
    assert mypy_policy(tmp_path, [module]).flags == ()
    from towel.mypy_ci_policy import declared_mypy_root

    assert declared_mypy_root(module) is None


@pytest.mark.parametrize(
    "command",
    [r"my\py --str\ict pkg", '"my"py --str"ict" pkg'],
)
def test_shell_quoted_literal_checker_and_flag_keep_strict_policy(
    tmp_path: Path, command: str
) -> None:
    module = _project(tmp_path, "|\n          " + command)
    assert mypy_policy(tmp_path, [module]).flags == ("--strict",)


@pytest.mark.parametrize(
    "command",
    [
        r"echo setup; my\py --str\ict pkg",
        'echo setup && "my"py --str"ict" pkg',
        r"my\py --str\ict $PACKAGE",
    ],
)
def test_shell_quoted_strict_tokens_cannot_downgrade_unsupported_commands(
    tmp_path: Path, command: str
) -> None:
    module = _project(tmp_path, "|\n          " + command)
    with pytest.raises(UnsupportedMypyPolicy):
        mypy_policy(tmp_path, [module])
    oracle = MypyInferrer()
    try:
        assert isinstance(oracle.check_project({str(module): module.read_text()}), CheckFailure)
    finally:
        oracle.close()


def test_shell_quoted_strict_tokens_cannot_downgrade_unsupported_yaml(tmp_path: Path) -> None:
    module = _project(tmp_path, r"my\py --str\ict pkg")
    (tmp_path / ".github/workflows/ci.yml").write_text(
        r"jobs: {lint: {steps: [{run: my\py --str\ict pkg}]}}" + "\n"
    )
    with pytest.raises(UnsupportedMypyPolicy):
        mypy_policy(tmp_path, [module])
