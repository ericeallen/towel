# Copyright 2026 Eric Allen
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

"""Regression tests for Audit Finding 8 (the naming workflow)."""

from __future__ import annotations

import json
from pathlib import Path
import textwrap

from tests.test_cli_integration import invoke
from towel.cli import _find_extracted_helpers, _generate_llm_prompt, helper_inventory

WRAPPED_SIGNATURE_MODULE = textwrap.dedent("""
    def __extracted_func_0(
        items: list[dict[str, object]],
        __param_0: str,
        __param_1: float,
        __param_2: float,
        __param_3: str,
        __param_4: int,
        __param_5: bool,
    ) -> dict[str, object]:
        filtered = [row for row in items if row.get("status") == __param_0]
        gross = round(sum(float(row.get("amount", 0.0)) for row in filtered), __param_4)
        fee = round(gross * __param_1 + __param_2, __param_4)
        net = round(gross - fee, __param_4)
        return {"label": __param_3, "net": net, "empty": not filtered and __param_5}


    def summarize_paid(orders: list[dict[str, object]]) -> dict[str, object]:
        return __extracted_func_0(orders, "paid", 0.029, 0.30, "paid_summary", 2, True)
    """).lstrip()


ORDER_SERVICE_BEFORE = textwrap.dedent("""
    from __future__ import annotations


    def register_customer(payload: dict[str, str]) -> dict[str, object]:
        raw_email = payload.get("email", "").strip().lower()
        raw_name = " ".join(payload.get("name", "").strip().split())
        if not raw_email or "@" not in raw_email:
            raise ValueError("Invalid email address")
        if len(raw_name) < 2:
            raise ValueError("Customer name is too short")
        domain = raw_email.split("@", 1)[1]
        return {
            "action": "created",
            "email": raw_email,
            "name": raw_name,
            "domain": domain,
            "verified": False,
        }


    def update_customer_contact(payload: dict[str, str], customer_id: int) -> dict[str, object]:
        raw_email = payload.get("email", "").strip().lower()
        raw_name = " ".join(payload.get("name", "").strip().split())
        if not raw_email or "@" not in raw_email:
            raise ValueError("Invalid email address")
        if len(raw_name) < 2:
            raise ValueError("Customer name is too short")
        domain = raw_email.split("@", 1)[1]
        return {
            "action": "updated",
            "customer_id": customer_id,
            "email": raw_email,
            "name": raw_name,
            "domain": domain,
        }


    def summarize_paid_orders(orders: list[dict[str, object]]) -> dict[str, float]:
        matching: list[float] = []
        for item in orders:
            if item.get("status") == "paid":
                amount = float(item.get("amount", 0.0))
                if amount > 0:
                    matching.append(amount)
        gross = round(sum(matching), 2)
        fees = round(gross * 0.029, 2)
        net = round(gross - fees, 2)
        average = round(gross / len(matching), 2) if matching else 0.0
        return {
            "count": float(len(matching)),
            "gross": gross,
            "fees": fees,
            "net": net,
            "average": average,
        }


    def summarize_refunded_orders(orders: list[dict[str, object]]) -> dict[str, float]:
        matching: list[float] = []
        for item in orders:
            if item.get("status") == "refunded":
                amount = float(item.get("amount", 0.0))
                if amount > 0:
                    matching.append(amount)
        gross = round(sum(matching), 2)
        fees = round(gross * 0.015, 2)
        net = round(gross - fees, 2)
        average = round(gross / len(matching), 2) if matching else 0.0
        return {
            "count": float(len(matching)),
            "gross": gross,
            "fees": fees,
            "net": net,
            "average": average,
        }


    class ReportExporter:
        def __init__(self, columns: list[str]) -> None:
            self.columns = columns

        def to_csv(self, rows: list[dict[str, object]]) -> str:
            if not self.columns:
                raise ValueError("Columns cannot be empty")
            lines = [",".join(self.columns)]
            for row in rows:
                cells = [str(row.get(col, "")).replace("\\n", " ").strip() for col in self.columns]
                lines.append(",".join(cells))
            return "\\n".join(lines)

        def to_tsv(self, rows: list[dict[str, object]]) -> str:
            if not self.columns:
                raise ValueError("Columns cannot be empty")
            lines = ["\\t".join(self.columns)]
            for row in rows:
                cells = [str(row.get(col, "")).replace("\\n", " ").strip() for col in self.columns]
                lines.append("\\t".join(cells))
            return "\\n".join(lines)
    """).lstrip()


def test_prompt_shows_full_wrapped_helper_call_site_and_parameter_keys(tmp_path: Path) -> None:
    """Audit Finding 8, item 1: prompt shows full source, a call site, and __param_N keys."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    module = tmp_path / "orders.py"
    module.write_text(WRAPPED_SIGNATURE_MODULE, encoding="utf-8")

    helpers = _find_extracted_helpers(tmp_path, None, None)
    assert len(helpers) == 1
    _, _, _, helper_source = helpers[0]
    # Signature alone wraps over 9 lines; _find_extracted_helpers must carry the full body.
    assert 'return {"label": __param_3, "net": net, "empty": not filtered and __param_5}' in (
        helper_source
    )

    prompt = _generate_llm_prompt(tmp_path, helpers, "generic")
    assert 'return {"label": __param_3, "net": net, "empty": not filtered and __param_5}' in prompt
    assert "orders.py:__extracted_func_0" in prompt
    for index in range(6):
        assert f"orders.py:__extracted_func_0.__param_{index}" in prompt
    assert '__extracted_func_0(orders, "paid", 0.029, 0.30, "paid_summary", 2, True)' in prompt
    assert "parameter" in prompt.lower()
    example_match = prompt.split("For example:", 1)[1]
    json_block = example_match.split("```json", 1)[1].split("```", 1)[0]
    parsed_example = json.loads(json_block)
    assert any(".__param_" in key for key in parsed_example)


def test_prompt_on_extracted_order_service_shows_bodies_call_sites_and_param_keys(
    tmp_path: Path,
) -> None:
    """Audit Finding 8, item 1: Gemini order_service reproduction includes bodies and param keys."""
    src = tmp_path / "src"
    src.mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'orders'\n", encoding="utf-8")
    (src / "order_service.py").write_text(ORDER_SERVICE_BEFORE, encoding="utf-8")
    out = tmp_path / "cleaned"
    dry_result = invoke(["dry", str(src), str(out), "--no-interactive", "--progress", "none"])
    assert dry_result.status == 0, dry_result.stderr

    helpers = _find_extracted_helpers(out, None, None)
    assert len(helpers) >= 2
    inventory = helper_inventory(out, helpers)
    prompt = _generate_llm_prompt(out, helpers, "generic")

    for entry in inventory["helpers"]:
        assert entry["source"] in prompt
        assert entry["rename_key"] in prompt
        assert entry["calls"], f"expected call sites for {entry['name']}"
        assert entry["calls"][0]["statement"] in prompt
        for param in entry["parameters"]:
            if param["name"].startswith("__param_"):
                assert param["rename_key"] in prompt


def test_unknown_mapping_keys_exit_2_and_list_existing_keys(tmp_path: Path) -> None:
    """Audit Finding 8, item 2: unknown helper, parameter, or file key exits 2 with existing keys."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    (tmp_path / "orders.py").write_text(
        "def __extracted_func_0(__param_0, items):\n"
        "    return [item for item in items if item == __param_0]\n\n"
        "def paid(rows):\n"
        "    return __extracted_func_0('paid', rows)\n",
        encoding="utf-8",
    )
    mapping = tmp_path / "renames.json"

    for bad_key, new_value in (
        ("orders.py:__extracted_func_99", "filter_paid"),
        ("__extracted_func_99", "filter_paid"),
        ("orders.py:__extracted_func_0.__param_9", "status"),
        ("missing.py:__extracted_func_0", "filter_paid"),
    ):
        mapping.write_text(json.dumps({bad_key: new_value}), encoding="utf-8")
        human = invoke(["rename-helpers", str(tmp_path), "--rename-file", str(mapping)])
        assert human.status == 2, f"expected exit 2 for {bad_key}, got {human}"
        assert bad_key in human.stderr
        assert "orders.py:__extracted_func_0" in human.stderr
        assert "orders.py:__extracted_func_0.__param_0" in human.stderr

        as_json = invoke(["rename-helpers", str(tmp_path), "--rename-file", str(mapping), "--json"])
        assert as_json.status == 2
        payload = json.loads(as_json.stdout)
        assert set(payload) == {"applied", "dry_run", "error"}
        assert payload["applied"] is False
        assert bad_key in payload["error"]
        assert "orders.py:__extracted_func_0" in payload["error"]

    # An unapplied helper paired with a nonexistent __param_0 mapped to an existing parameter
    # name ("items") must also fail with exit 2 rather than pretending it was already applied.
    mapping.write_text(
        json.dumps(
            {
                "orders.py:__extracted_func_0": "filter_by_status",
                "orders.py:__extracted_func_0.__param_9": "items",
            }
        ),
        encoding="utf-8",
    )
    bogus = invoke(["rename-helpers", str(tmp_path), "--rename-file", str(mapping)])
    assert bogus.status == 2
    assert "orders.py:__extracted_func_0.__param_9" in bogus.stderr


def test_applying_mapping_a_second_time_succeeds_and_reports_already_applied(
    tmp_path: Path,
) -> None:
    """Audit Finding 8, item 2: repeating an already-applied mapping exits 0 with 'already applied'."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    source_file = tmp_path / "orders.py"
    source_file.write_text(
        "def __extracted_func_0(__param_0, items):\n"
        "    return [item for item in items if item == __param_0]\n\n"
        "class Box:\n"
        "    def __extracted_func_1(self, __param_0):\n"
        "        return self.width * __param_0\n\n"
        "    def area(self):\n"
        "        return self.__extracted_func_1(2)\n\n"
        "def paid(rows):\n"
        "    return __extracted_func_0('paid', rows)\n",
        encoding="utf-8",
    )
    mapping = tmp_path / "renames.json"
    mapping.write_text(
        json.dumps(
            {
                "orders.py:__extracted_func_0": "filter_by_status",
                "orders.py:__extracted_func_0.__param_0": "target_status",
                "orders.py:Box.__extracted_func_1": "__scaled_width",
                "orders.py:Box.__extracted_func_1.__param_0": "factor",
            }
        ),
        encoding="utf-8",
    )

    first = invoke(["rename-helpers", str(tmp_path), "--rename-file", str(mapping)])
    assert first.status == 0, first.stderr
    renamed_source = source_file.read_text(encoding="utf-8")
    assert "def filter_by_status(target_status, items):" in renamed_source
    assert "def __scaled_width(self, factor):" in renamed_source

    second = invoke(["rename-helpers", str(tmp_path), "--rename-file", str(mapping)])
    assert second.status == 0, second.stderr
    assert "already applied" in second.stdout
    assert source_file.read_text(encoding="utf-8") == renamed_source

    second_json = invoke(["rename-helpers", str(tmp_path), "--rename-file", str(mapping), "--json"])
    assert second_json.status == 0, second_json.stderr
    payload = json.loads(second_json.stdout)
    assert set(payload) == {"applied", "dry_run", "changes", "renames"}
    assert payload["applied"] is True and payload["changes"] == 0


def test_rename_preview_prints_unified_diff_before_change_count(tmp_path: Path) -> None:
    """Audit Finding 8, item 3: --preview prints the unified diff, then the count."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    original = (
        "def __extracted_func_0(__param_0):\n"
        "    return __param_0 * 2\n\n"
        "def caller(x):\n"
        "    return __extracted_func_0(x)\n"
    )
    source = tmp_path / "orders.py"
    source.write_text(original, encoding="utf-8")
    mapping = tmp_path / "renames.json"
    mapping.write_text(
        json.dumps(
            {
                "orders.py:__extracted_func_0": "double_amount",
                "orders.py:__extracted_func_0.__param_0": "amount",
            }
        ),
        encoding="utf-8",
    )

    preview = invoke(["rename-helpers", str(tmp_path), "--rename-file", str(mapping), "--preview"])
    assert preview.status == 0, preview.stderr
    assert "--- before/orders.py" in preview.stdout
    assert "+++ after/orders.py" in preview.stdout
    assert "-def __extracted_func_0(__param_0):" in preview.stdout
    assert "+def double_amount(amount):" in preview.stdout
    assert "[PREVIEW] Would make 4 change(s)" in preview.stdout
    assert preview.stdout.index("+++ after/orders.py") < preview.stdout.index(
        "[PREVIEW] Would make 4 change(s)"
    )
    assert source.read_text(encoding="utf-8") == original

    # Interactive --preview must also print the unified diff before the count.
    interactive = invoke(
        ["rename-helpers", str(tmp_path), "--preview"],
        stdin=mapping.read_text(encoding="utf-8"),
    )
    assert interactive.status == 0, interactive.stderr
    assert "--- before/orders.py" in interactive.stdout
    assert "+def double_amount(amount):" in interactive.stdout
    assert interactive.stdout.index("+++ after/orders.py") < interactive.stdout.index(
        "[PREVIEW] Would make 4 change(s)"
    )

    # --preview --json keeps stdout pure JSON.
    preview_json = invoke(
        ["rename-helpers", str(tmp_path), "--rename-file", str(mapping), "--preview", "--json"]
    )
    assert preview_json.status == 0
    assert json.loads(preview_json.stdout)["changes"] == 4


def test_recover_returns_restored_paths_and_prints_summary(tmp_path: Path) -> None:
    """Audit Finding 8, item 4: changes.recover returns paths and CLI prints restored count."""
    import os
    from unittest.mock import patch

    from towel.changes import ChangePlan, RecoveryRequired, apply_changes, recover

    files = [tmp_path / "a.py", tmp_path / "b.py", tmp_path / "c.py"]
    for path in files:
        path.write_bytes(b"value = 1\n")
    plan = ChangePlan.from_sources(
        {str(p): p.read_bytes() for p in files},
        {str(p): "value = 2\n" for p in files},
    )
    real_replace = os.replace
    writes = 0

    def fail_on_third(src_path: object, dst_path: object) -> None:
        nonlocal writes
        if Path(str(dst_path)) in files:
            writes += 1
            if writes > 2:
                raise OSError("simulated interruption")
        real_replace(str(src_path), str(dst_path))

    with patch("towel.changes.os.replace", side_effect=fail_on_third):
        try:
            apply_changes(plan)
        except RecoveryRequired:
            pass
    journal = next(tmp_path.glob(".towel-transaction-*"))
    restored = recover(journal)
    assert restored == [files[0].resolve(), files[1].resolve()]
    assert not journal.exists()

    # Now verify the CLI message on another 2-file interrupted transaction.
    writes = 0
    with patch("towel.changes.os.replace", side_effect=fail_on_third):
        try:
            apply_changes(plan)
        except RecoveryRequired:
            pass
    cli_journal = next(tmp_path.glob(".towel-transaction-*"))
    result = invoke(["recover", str(cli_journal)])
    assert result.status == 0, result.stderr
    assert result.stdout == "Restored 2 files; journal removed.\n"
    assert not cli_journal.exists()


def test_scope_refusal_uses_plain_wording(tmp_path: Path) -> None:
    """Audit Finding 8, item 5: unmarked directory refusal explains missing markers plainly."""
    bare = tmp_path / "bare_dir"
    bare.mkdir()
    (bare / "m.py").write_text("def __extracted_func_0(x):\n    return x + 1\n", encoding="utf-8")
    mapping = tmp_path / "renames.json"
    mapping.write_text(json.dumps({"__extracted_func_0": "increment"}), encoding="utf-8")

    human = invoke(["rename-helpers", str(bare), "--rename-file", str(mapping)])
    assert human.status == 2
    assert "Cannot establish the consumer scope" not in human.stderr
    assert "No pyproject.toml, setup.cfg, setup.py or package __init__.py was found above" in (
        human.stderr
    )
    assert "Towel cannot tell whether code outside it calls these helpers" in human.stderr
    assert "if nothing outside does, add --complete-scope" in human.stderr

    as_json = invoke(["rename-helpers", str(bare), "--rename-file", str(mapping), "--json"])
    assert as_json.status == 2
    payload = json.loads(as_json.stdout)
    assert payload["applied"] is False
    assert "No pyproject.toml, setup.cfg, setup.py or package __init__.py was found above" in (
        payload["error"]
    )
    assert "--complete-scope" in payload["error"]


def test_llm_flag_is_hidden_from_help_accepted_and_emits_one_deprecation_line(
    tmp_path: Path,
) -> None:
    """Audit Finding 8, item 6: --llm is suppressed in --help and prints one deprecation line."""
    help_result = invoke(["rename-helpers", "--help"])
    assert help_result.status == 0
    assert "--llm" not in help_result.stdout

    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    (tmp_path / "m.py").write_text(
        "def __extracted_func_0(x):\n    return x * 2\n", encoding="utf-8"
    )

    without_llm = invoke(["rename-helpers", str(tmp_path), "--list"])
    assert without_llm.status == 0
    assert "--llm" not in without_llm.stderr

    with_llm = invoke(["rename-helpers", str(tmp_path), "--list", "--llm", "claude"])
    assert with_llm.status == 0
    stderr_lines = [line for line in with_llm.stderr.splitlines() if line.strip()]
    assert len(stderr_lines) == 1
    assert "--llm" in stderr_lines[0] and "deprecated" in stderr_lines[0].lower()
