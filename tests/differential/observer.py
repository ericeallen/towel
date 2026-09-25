"""Observe what a program does: import its modules, describe them, run probes, write JSON.

Usage: ``python -P observer.py PROJECT_ROOT SPEC_JSON OUT_JSON``

The runner starts this script in a fresh interpreter, the one under test,
once for the original project and once for the refactored copy. It imports
only the standard library and never Towel, so what it records is the
program's own behaviour. ``-P`` keeps this directory off ``sys.path``; the
project root goes first instead.

It records everything a program could observe that Towel promises to keep:

- for each import, in order: whether it succeeded, what it printed to
  stdout and stderr, the warnings it raised, and the project modules it
  loaded;
- for every loaded project module, before the probes and again after them
  (so state written at run time is compared too): its names and ``dir()``,
  and each function's signature, qualified name, module, defaults,
  annotations and docstring, and each class's qualified name, MRO,
  metaclass, ``__dict__`` keys, ``dir()``, members and dataclass fields;
- for each probe (see :mod:`tests.differential.cases`): stdout, stderr and
  warnings, then the value's ``repr`` and a ``pickle`` round trip of it, or
  the exception's type and message. A call probe records each call on its
  own, with its arguments after the call.

Towel's helpers (``*_extracted_func_N``) are left out of every list of
names, and memory addresses and the project's path are normalized out of
every text. A call that runs longer than the spec's ``call_seconds`` is
interrupted and recorded as a timeout, and nothing after it is observed:
the program's state is no longer one the original could reach.
"""

from __future__ import annotations

import contextlib
import copy
import importlib
import inspect
import io
import json
import os
import pickle
import re
import signal
import sys
import types
import warnings
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional, Sequence, Set, Tuple

HELPER_NAME = re.compile(r"_extracted_func(_\d+)?$")
"""A name Towel gave a helper it wrote, mangled or not."""

_ADDRESS = re.compile(r"0x[0-9a-fA-F]+")
_TEXT_LIMIT = 20_000
_REPR_LIMIT = 2_000
_UNDESCRIBED_MODULE_KEYS = frozenset(
    {
        "__builtins__",
        "__file__",
        "__cached__",
        "__loader__",
        "__spec__",
        "__path__",
        "__name__",
        "__package__",
        "__doc__",
    }
)
_UNDESCRIBED_CLASS_KEYS = frozenset(
    {
        "__dict__",
        "__weakref__",
        "__module__",
        "__qualname__",
        "__firstlineno__",
        "__static_attributes__",
    }
)

Record = Dict[str, Any]


class CallTimeout(BaseException):
    """A probe call ran past its budget. Not an ``Exception``, so the program cannot catch it."""


class _Normalizer:
    """Rewrites text so that two runs from different directories compare alike."""

    def __init__(self, root: str) -> None:
        self._roots = sorted({root, os.path.realpath(root)}, key=len, reverse=True)

    def text(self, value: str, limit: int = _TEXT_LIMIT) -> str:
        for root in self._roots:
            value = value.replace(root, "<ROOT>")
        return _ADDRESS.sub("0xADDR", value)[:limit]

    def repr(self, value: object) -> str:
        try:
            shown = repr(value)
        except BaseException as error:  # noqa: BLE001 - any repr may raise
            shown = f"<repr raised {type(error).__name__}: {error}>"
        return self.text(shown, _REPR_LIMIT)

    def exception(self, error: BaseException) -> Record:
        return {
            "type": f"{type(error).__module__}.{type(error).__qualname__}",
            "message": self.text(str(error), _REPR_LIMIT),
        }


@contextlib.contextmanager
def _captured(record: Record, normalize: _Normalizer) -> Iterator[None]:
    """Record into ``record`` what the enclosed code prints and warns."""
    out, err = io.StringIO(), io.StringIO()
    saved = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("default")
            yield
    finally:
        sys.stdout, sys.stderr = saved
        record["stdout"] = normalize.text(out.getvalue())
        record["stderr"] = normalize.text(err.getvalue())
        record["warnings"] = [
            {
                "category": warning.category.__name__,
                "message": normalize.text(str(warning.message), 500),
                "file": os.path.basename(str(warning.filename)),
            }
            for warning in caught
        ]


@contextlib.contextmanager
def _time_limit(seconds: float) -> Iterator[None]:
    """Raise :class:`CallTimeout` in the enclosed code once ``seconds`` have passed."""

    def expire(signum: int, frame: Optional[types.FrameType]) -> None:
        raise CallTimeout(f"no result within {seconds}s")

    previous = signal.signal(signal.SIGALRM, expire)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


class _Describer:
    """The structure of modules and classes, as plain data."""

    def __init__(self, normalize: _Normalizer) -> None:
        self.normalize = normalize

    def function(self, function: Any) -> Record:
        described: Record = {"kind": "function"}
        try:
            described["signature"] = self.normalize.text(str(inspect.signature(function)))
        except (TypeError, ValueError) as error:
            described["signature"] = f"<no signature: {type(error).__name__}>"
        for attribute in ("__qualname__", "__name__", "__module__", "__doc__"):
            described[attribute] = getattr(function, attribute, None)
        described["defaults"] = self.normalize.repr(getattr(function, "__defaults__", None))
        described["kwdefaults"] = self.normalize.repr(getattr(function, "__kwdefaults__", None))
        try:
            described["annotations"] = self.normalize.repr(
                getattr(function, "__annotations__", None)
            )
        except BaseException as error:  # noqa: BLE001 - evaluating annotations may raise
            described["annotations"] = f"<raised {type(error).__name__}>"
        return described

    def member(self, value: Any) -> Record:
        if isinstance(value, (staticmethod, classmethod)):
            described = self.member(value.__func__)
            described["wrapper"] = type(value).__name__
            return described
        if isinstance(value, property):
            return {
                "kind": "property",
                "fget": self.member(value.fget) if value.fget else None,
                "fset": value.fset is not None,
            }
        if inspect.isfunction(value):
            return self.function(value)
        if inspect.isclass(value):
            return {"kind": "class", "qualname": value.__qualname__}
        return {"kind": type(value).__name__, "repr": self.normalize.repr(value)[:300]}

    def klass(self, cls: type, depth: int = 0) -> Record:
        try:
            keys = list(vars(cls))
        except TypeError:
            keys = []
        try:
            listed = dir(cls)
        except BaseException as error:  # noqa: BLE001 - a metaclass may define __dir__
            listed = [f"<dir raised {type(error).__name__}>"]
        members: Record = {}
        for key in keys:
            if key in _UNDESCRIBED_CLASS_KEYS or HELPER_NAME.search(key):
                continue
            value = vars(cls)[key]
            nested = inspect.isclass(value) and depth < 2
            members[key] = self.klass(value, depth + 1) if nested else self.member(value)
        described: Record = {
            "kind": "class",
            "qualname": getattr(cls, "__qualname__", None),
            "module": getattr(cls, "__module__", None),
            "mro": [getattr(base, "__qualname__", "?") for base in getattr(cls, "__mro__", ())],
            "metaclass": type(cls).__qualname__,
            "dict_keys": [key for key in keys if not HELPER_NAME.search(key)],
            "dir": [key for key in listed if not HELPER_NAME.search(key)],
            "members": members,
        }
        try:
            described["signature"] = self.normalize.text(str(inspect.signature(cls)))
        except (TypeError, ValueError) as error:
            described["signature"] = f"<no signature: {type(error).__name__}>"
        fields = getattr(cls, "__dataclass_fields__", None)
        if fields is not None:
            described["dataclass_fields"] = list(fields)
        return described

    def module(self, module: types.ModuleType) -> Record:
        namespace = vars(module)
        names = sorted(name for name in namespace if not HELPER_NAME.search(name))
        try:
            listed = [name for name in dir(module) if not HELPER_NAME.search(name)]
        except BaseException as error:  # noqa: BLE001 - a module may define __dir__
            listed = [f"<dir raised {type(error).__name__}>"]
        items: Record = {}
        for name in names:
            if name in _UNDESCRIBED_MODULE_KEYS:
                continue
            value = namespace[name]
            if inspect.isclass(value) and getattr(value, "__module__", None) == module.__name__:
                items[name] = self.klass(value)
            elif inspect.isclass(value):
                items[name] = {
                    "kind": "class-ref",
                    "qualname": getattr(value, "__qualname__", None),
                    "module": getattr(value, "__module__", None),
                }
            elif inspect.isfunction(value):
                items[name] = self.function(value)
            elif isinstance(value, types.ModuleType):
                items[name] = {"kind": "module", "name": value.__name__}
            else:
                items[name] = {"kind": type(value).__name__, "repr": self.normalize.repr(value)}
        return {
            "names": names,
            "dir": listed,
            "all": self.normalize.repr(namespace.get("__all__", "<none>")),
            "items": items,
        }


class _Observation:
    """One observation of one program: the imports, the modules, then the probes."""

    def __init__(self, root: str, spec: Mapping[str, Any]) -> None:
        self.normalize = _Normalizer(root)
        self.describe = _Describer(self.normalize)
        self.modules: List[List[str]] = list(spec["modules"])
        self.probes: List[Mapping[str, str]] = list(spec["probes"])
        self.call_seconds = float(spec.get("call_seconds", 2.0))
        self.prefixes = {name.split(".")[0] for _, name in self.modules}
        self.namespace: Dict[str, Any] = {}
        self.timed_out = False

    def project_modules(self) -> Set[str]:
        return {name for name in sys.modules if name.split(".")[0] in self.prefixes}

    def run(self) -> Record:
        record: Record = {"imports": [self.import_module(a, n) for a, n in self.modules]}
        record["modules"] = self.describe_project()
        record["probes"] = []
        for probe in self.probes:
            if self.timed_out:
                break
            record["probes"].append(self.probe(probe))
        record["timed_out"] = self.timed_out
        record["modules_after"] = {} if self.timed_out else self.describe_project()
        record["project_modules"] = sorted(self.project_modules())
        return record

    def import_module(self, alias: str, name: str) -> Record:
        before = self.project_modules()
        entry: Record = {"module": name}
        with _captured(entry, self.normalize):
            try:
                self.namespace[alias] = importlib.import_module(name)
                entry["ok"] = True
            except BaseException as error:  # noqa: BLE001 - the program's own failure is data
                entry["exception"] = self.normalize.exception(error)
        entry["new_modules"] = sorted(self.project_modules() - before)
        return entry

    def describe_project(self) -> Record:
        described: Record = {}
        for name in sorted(self.project_modules()):
            try:
                described[name] = self.describe.module(sys.modules[name])
            except BaseException as error:  # noqa: BLE001 - describing must not end the run
                described[name] = {"describe_raised": self.normalize.exception(error)}
        return described

    def attempt(self, entry: Record, produce: Callable[[], Any]) -> Tuple[bool, Any]:
        """Run ``produce`` under the time limit; record in ``entry`` how it failed, if it did."""
        try:
            with _time_limit(self.call_seconds):
                return True, produce()
        except CallTimeout:
            self.timed_out = True
            entry["timeout"] = True
        except BaseException as error:  # noqa: BLE001 - the program's own failure is data
            entry["exception"] = self.normalize.exception(error)
        return False, None

    def outcome(self, entry: Record, produce: Callable[[], Any]) -> None:
        """Run ``produce`` and record its value, or how it failed, in ``entry``."""
        produced, value = self.attempt(entry, produce)
        if produced:
            entry["value"] = self.normalize.repr(value)
            entry["type"] = type(value).__qualname__
            entry["pickle"] = self.pickled(value)

    def pickled(self, value: object) -> Record:
        try:
            data = pickle.dumps(value)
        except BaseException as error:  # noqa: BLE001 - unpicklable values are data
            return {"dumps": self.normalize.exception(error)}
        try:
            back = pickle.loads(data)  # noqa: S301 - the data was pickled just above
        except BaseException as error:  # noqa: BLE001
            return {"dumps": "ok", "loads": self.normalize.exception(error)}
        return {"dumps": "ok", "loads": "ok", "repr": self.normalize.repr(back)}

    def probe(self, probe: Mapping[str, str]) -> Record:
        kind = probe["kind"]
        entry: Record = {"kind": kind}
        if kind == "calls":
            entry["target"] = probe["target"]
            entry["calls"] = self.calls(entry, probe["target"], probe["arguments"])
            return entry
        namespace = self.namespace
        with _captured(entry, self.normalize):
            if kind == "value":
                entry["expression"] = expression = probe["expression"]
                self.outcome(entry, lambda: eval(expression, namespace))  # noqa: S307
            else:
                entry["source"] = source = probe["source"]
                self.attempt(entry, lambda: exec(source, namespace))  # noqa: S102
        return entry

    def calls(self, entry: Record, target: str, arguments: str) -> List[Record]:
        """Each call of ``target`` with one of the argument tuples ``arguments`` evaluates to."""
        namespace = self.namespace
        with _captured(entry, self.normalize):
            found, function = self.attempt(entry, lambda: eval(target, namespace))  # noqa: S307
            listed, argument_sets = self.attempt(
                entry, lambda: list(eval(arguments, namespace))  # noqa: S307
            )
        if not (found and listed):
            return []
        calls: List[Record] = []
        for given in argument_sets:
            if self.timed_out:
                break
            passed = copy.deepcopy(given)
            call: Record = {"arguments": self.normalize.repr(passed)}
            with _captured(call, self.normalize):
                self.outcome(call, lambda: function(*passed))
            call["arguments_after"] = self.normalize.repr(passed)
            calls.append(call)
        return calls


def observe(root: str, spec: Mapping[str, Any]) -> Record:
    """Observe the project at ``root`` as ``spec`` directs; run it with the root on ``sys.path``."""
    sys.path.insert(0, root)
    os.chdir(root)
    sys.dont_write_bytecode = True
    return _Observation(root, spec).run()


def main(argv: Sequence[str]) -> int:
    root, spec_path, out_path = argv
    with open(spec_path, encoding="utf-8") as handle:
        spec = json.load(handle)
    record = observe(os.path.realpath(root), spec)
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(record, handle, indent=1, sort_keys=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
