"""The CLI checks its output tree and owns the checker's entire lifetime."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from towel import cli
from towel.type_inference import TypeOracle
from tests.test_cli_integration import invoke


@pytest.mark.parametrize("fail", [False, True])
def test_output_oracle_closes_on_success_and_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail: bool
) -> None:
    source = tmp_path / "input"
    source.mkdir()
    (source / "main.py").write_text("value = 3\n")
    output = tmp_path / "output"
    oracle = Mock(spec=TypeOracle)

    def select(path: Path) -> TypeOracle:
        assert path == source
        assert (output / "main.py").read_text() == "value = 3\n"
        return oracle

    def sidecar(*args: object) -> None:
        if fail:
            raise OSError("simulated output failure")

    monkeypatch.setattr(cli, "_type_oracle", select)
    monkeypatch.setattr(cli, "_write_change_sidecar", sidecar)
    result = invoke(
        ["dry", str(source), str(output), "--no-interactive", "--no-format", "--progress", "none"]
    )
    assert result.status == int(fail)
    oracle.close.assert_called_once_with()


def test_cancelled_run_never_constructs_checker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "input.py"
    source.write_text("value = 3\n")
    selector = Mock()
    monkeypatch.setattr(cli, "_type_oracle", selector)
    result = invoke(["dry", str(source), str(tmp_path / "output.py"), "--interactive"], stdin="n\n")
    assert result.status == 0
    selector.assert_not_called()
    assert not (tmp_path / "output.py").exists()
