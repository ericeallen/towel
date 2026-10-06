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

"""Malformed LSP publications must fail, never erase the last known diagnostics."""

import copy
import json
from pathlib import Path
import queue
import sys

import pytest

from towel import pyright_session, type_inference
from towel.pyright_session import Diagnostic, PyrightSession, SessionFailure
from towel.type_inference import CheckFailure, CheckSuccess, PyrightOracle
from towel.unification.exceptions import RefactoringError
from towel.unification.refactor_engine import UnificationRefactorEngine


def diagnostic(**changes: object) -> dict[str, object]:
    return {
        "range": {"start": {"line": 2, "character": 3}, "end": {"line": 2, "character": 9}},
        "message": "known error",
        "severity": 1,
        "code": "reportAssignmentType",
        **changes,
    }


def session_state(path: Path) -> PyrightSession:
    session = PyrightSession.__new__(PyrightSession)
    session._state = {str(path): [Diagnostic(str(path), 2, "error", "known error")]}
    session._marker = pyright_session._Marker()
    session._marker_answered = False
    session._events = queue.Queue()
    return session


BAD_ENTRIES: tuple[object, ...] = (
    "not an object",
    diagnostic(message=7),
    diagnostic(range=None),
    diagnostic(range={"start": {"line": -1, "character": 0}, "end": {"line": 2, "character": 0}}),
    diagnostic(range={"start": {"line": True, "character": 0}, "end": {"line": 2, "character": 0}}),
    diagnostic(range={"start": {"line": 2, "character": "3"}, "end": {"line": 2, "character": 9}}),
    diagnostic(range={"start": {"line": 2, "character": 9}, "end": {"line": 2, "character": 3}}),
    diagnostic(severity="error"),
    diagnostic(severity={}),
    diagnostic(severity=True),
    diagnostic(severity=0),
    diagnostic(code=[]),
)


@pytest.mark.parametrize("entry", BAD_ENTRIES)
def test_a_malformed_tail_does_not_partially_replace_known_errors(
    tmp_path: Path, entry: object
) -> None:
    path = tmp_path / "m.py"
    session = session_state(path)
    before = copy.deepcopy(session._state)
    params = {"uri": path.as_uri(), "diagnostics": [diagnostic(message="new message"), entry]}
    original = copy.deepcopy(params)
    with pytest.raises(SessionFailure, match="pyright diagnostic"):
        session._record(params)
    assert session._state == before and params == original


@pytest.mark.parametrize("value", [None, "not-an-array", {}, 7])
def test_nonarray_diagnostics_fail_instead_of_clearing(tmp_path: Path, value: object) -> None:
    path = tmp_path / "m.py"
    session = session_state(path)
    with pytest.raises(SessionFailure):
        session._record({"uri": path.as_uri(), "diagnostics": value})
    assert session._state[str(path)][0].message == "known error"


@pytest.mark.parametrize(
    "params",
    [
        None,
        [],
        {"diagnostics": []},
        {"uri": 7, "diagnostics": []},
        {"uri": "file://[bad", "diagnostics": []},
        {"uri": "not-a-file-uri", "diagnostics": []},
    ],
)
def test_malformed_publications_in_the_drain_path_fail(tmp_path: Path, params: object) -> None:
    path = tmp_path / "m.py"
    session = session_state(path)
    session._events.put(("textDocument/publishDiagnostics", params))
    with pytest.raises(SessionFailure):
        session._drain()
    assert session._state[str(path)][0].message == "known error"


def test_empty_diagnostics_explicitly_clear_and_related_information_is_accepted(
    tmp_path: Path,
) -> None:
    path = tmp_path / "m.py"
    session = session_state(path)
    related = [
        {
            "location": {"uri": path.as_uri(), "range": diagnostic()["range"]},
            "message": "related declaration",
        }
    ]
    entry = diagnostic(
        relatedInformation=related, tags=[1], data={"serverExtension": True}, code=42
    )
    session._record({"uri": path.as_uri(), "diagnostics": [entry]})
    assert session._state[str(path)] == [Diagnostic(str(path), 2, "error", "known error")]
    session._record({"uri": path.as_uri(), "diagnostics": []})
    assert session._state == {}


def test_malformed_marker_publication_cannot_acknowledge_analysis(tmp_path: Path) -> None:
    path = tmp_path / pyright_session._MARKER_NAME
    session = session_state(tmp_path / "m.py")
    session._marker.token = "current-token"
    with pytest.raises(SessionFailure):
        session._record(
            {"uri": path.as_uri(), "diagnostics": [diagnostic(message="current-token"), "bad"]}
        )
    assert not session._marker_answered and len(session._state) == 1


SERVER = """import json, sys
from pathlib import Path
uri, malformed = sys.argv[1], json.loads(sys.argv[2])

def write(message):
    data = json.dumps(message).encode()
    sys.stdout.buffer.write(b'Content-Length: %d\\r\\n\\r\\n' % len(data) + data)
    sys.stdout.buffer.flush()

def read():
    header = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        if line == b'\\r\\n':
            break
        key, value = line.decode().split(':', 1)
        header[key.lower()] = value.strip()
    return json.loads(sys.stdin.buffer.read(int(header['content-length'])))

while True:
    message = read()
    if message is None:
        break
    if message.get('method') == 'initialize':
        write({'jsonrpc':'2.0', 'id':message['id'], 'result':{'capabilities':{}}})
    elif message.get('method') == 'initialized':
        write({'jsonrpc':'2.0', 'method':'textDocument/publishDiagnostics', 'params':{'uri':uri, 'diagnostics':[{'message':'known error','severity':1,'range':{'start':{'line':0,'character':0},'end':{'line':0,'character':1}}}]}})
        write({'jsonrpc':'2.0', 'method':'textDocument/publishDiagnostics', 'params':malformed})
    elif message.get('method') == 'shutdown':
        write({'jsonrpc':'2.0', 'id':message['id'], 'result':None})
    elif message.get('method') == 'exit':
        break
"""


def fake_server(tmp_path: Path, params: object) -> list[str]:
    script = tmp_path / "server.py"
    script.write_text(SERVER)
    return [sys.executable, str(script), (tmp_path / "m.py").as_uri(), json.dumps(params)]


@pytest.mark.parametrize(
    "params", [None, {"diagnostics": []}, {"uri": "file:///m.py", "diagnostics": "bad"}]
)
def test_fake_server_malformed_wire_notification_ends_session(
    tmp_path: Path, params: object
) -> None:
    with pytest.raises(SessionFailure, match="pyright diagnostic"):
        PyrightSession(fake_server(tmp_path, params), tmp_path, sys.executable)


COLD = """import json, sys
from pathlib import Path
root = Path(sys.argv[sys.argv.index('--project')+1])
errors = [{'file':str(path),'severity':'error','message':'candidate is broken','range':{'start':{'line':0,'character':0},'end':{'line':0,'character':1}},'rule':'reportAssignmentType'} for path in root.rglob('*.py') if 'BROKEN_CANDIDATE' in path.read_text()]
print(json.dumps({'version':'fixture','generalDiagnostics':errors,'summary':{'errorCount':len(errors),'warningCount':0,'informationCount':0,'timeInSec':0}}))
raise SystemExit(bool(errors))
"""


def test_malformed_server_falls_back_and_cannot_certify_a_broken_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    path = root / "m.py"
    path.write_text("value = 1\n")
    (root / "pyrightconfig.json").write_text('{"include":["m.py"]}')
    cold = tmp_path / "cold.py"
    cold.write_text(COLD)
    command = fake_server(tmp_path, {"uri": path.as_uri(), "diagnostics": "malformed"})
    monkeypatch.setattr(type_inference, "_pyright_langserver_command", lambda: command)
    monkeypatch.setattr(type_inference, "_pyright_command", lambda: [sys.executable, str(cold)])
    oracle = PyrightOracle()
    try:
        baseline = oracle.check_project({str(path): path.read_text()})
        rejected = oracle.check_project({str(path): "BROKEN_CANDIDATE = 'bad'\n"})
        assert oracle._server is None and not oracle._warmed
    finally:
        oracle.close()
    assert isinstance(baseline, CheckSuccess) and baseline.errors == ()
    assert isinstance(rejected, CheckSuccess) and len(rejected.errors) == 1
    assert "candidate is broken" in rejected.errors[0].message
    assert path.read_text() == "value = 1\n"


def test_malformed_server_and_failing_cold_tool_are_not_a_clean_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    path = root / "m.py"
    path.write_text("value = 1\n")
    (root / "pyrightconfig.json").write_text('{"include":["m.py"]}')
    command = fake_server(tmp_path, {"uri": path.as_uri(), "diagnostics": [7]})
    monkeypatch.setattr(type_inference, "_pyright_langserver_command", lambda: command)
    monkeypatch.setattr(
        type_inference, "_pyright_command", lambda: [sys.executable, "-c", "raise SystemExit(3)"]
    )
    oracle = PyrightOracle()
    try:
        result = oracle.check_project({str(path): "value = 2\n"})
    finally:
        oracle.close()
    assert isinstance(result, CheckFailure)
    assert path.read_text() == "value = 1\n"


def test_failed_fallback_prevents_publication_and_preserves_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    path = root / "m.py"
    source = "".join(
        f"def {name}(value: int) -> int:\n"
        "    total = value + 1\n    doubled = total * 2\n"
        f"    answer = doubled - {offset}\n    return answer\n\n"
        for name, offset in (("first", 3), ("second", 4))
    )
    path.write_text(source)
    (root / "pyrightconfig.json").write_text('{"include":["m.py"]}')
    command = fake_server(tmp_path, {"uri": path.as_uri(), "diagnostics": [7]})
    monkeypatch.setattr(type_inference, "_pyright_langserver_command", lambda: command)
    monkeypatch.setattr(
        type_inference, "_pyright_command", lambda: [sys.executable, "-c", "raise SystemExit(3)"]
    )
    output = tmp_path / "output"
    oracle = PyrightOracle()
    try:
        engine = UnificationRefactorEngine(type_oracle=oracle, snippet_formatter=lambda text: text)
        with pytest.raises(
            RefactoringError, match="Original project type check failed: pyright produced no JSON"
        ):
            engine.refactor_directory_to_fixed_point(str(root), str(output), progress="none")
    finally:
        oracle.close()
    assert path.read_text() == source
    assert not output.exists()
