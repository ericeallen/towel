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

"""Loggers, and the settings Towel reads from its environment.

The library never prints diagnostics. Warnings that a user should see (a
skipped file, a parallel pool that fell back to serial, modules that inspect
their own frames) go to the ``towel`` logger, which Python routes to stderr
even when nothing configures logging. Debugging output that explains why a
pair was rejected, what a checker revealed, or which overlapping proposals
were dropped goes to the child loggers below at DEBUG level, off unless
enabled. :class:`Settings` reads the environment once, at engine
construction, so no other module consults ``os.environ``: the documented
variables (``TOWEL_WORKERS``, ``TOWEL_CHECK_AST_IMMUTABLE``,
``DEBUG_PROPOSAL_REJECTIONS``, ``DEBUG_VALIDATION``, ``DEBUG_OVERLAP_FILTER``,
``TOWEL_DEBUG_TYPES``) keep working through it.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os
import sys
from typing import Mapping, Optional, TextIO

LOG = logging.getLogger("towel")
"""User-facing notes and warnings from the library."""

REJECTIONS = logging.getLogger("towel.rejections")
"""Why each candidate pair was declined (``DEBUG_PROPOSAL_REJECTIONS``)."""

VALIDATION = logging.getLogger("towel.validation")
"""Step-by-step trace of a pair's validation (``DEBUG_VALIDATION``)."""

OVERLAP = logging.getLogger("towel.overlap")
"""Which overlapping proposals were kept and dropped (``DEBUG_OVERLAP_FILTER``)."""

TYPES = logging.getLogger("towel.types")
"""What the type checker revealed and the errors that made annotations fall back (``TOWEL_DEBUG_TYPES``)."""

UNIFIER = logging.getLogger("towel.unifier")
"""Recovered internal failures inside unification; never on by an environment variable."""


def debugging(logger: logging.Logger) -> bool:
    """Whether ``logger`` will emit DEBUG records, so callers can skip building them."""
    return logger.isEnabledFor(logging.DEBUG)


@dataclass(frozen=True)
class Settings:
    """Everything Towel takes from environment variables, read once."""

    workers: Optional[int]
    """``TOWEL_WORKERS``: 1 keeps analysis serial, N caps the forked workers; None lets the engine decide."""

    check_ast_immutable: bool
    """``TOWEL_CHECK_AST_IMMUTABLE``: verify on every cache reuse that analysis left the AST untouched."""

    debug_rejections: bool
    debug_validation: bool
    debug_overlap: bool
    debug_types: bool

    @classmethod
    def from_environ(cls, environ: Optional[Mapping[str, str]] = None) -> "Settings":
        """Read the documented variables from ``environ`` (the process environment by default)."""
        env = os.environ if environ is None else environ
        workers: Optional[int] = None
        raw_workers = env.get("TOWEL_WORKERS")
        if raw_workers is not None:
            try:
                workers = int(raw_workers)
                if workers < 1:
                    LOG.warning(
                        "TOWEL_WORKERS=%r is not positive; evaluating pairs serially", raw_workers
                    )
                    workers = 1
            except ValueError:
                LOG.warning(
                    "TOWEL_WORKERS=%r is not an integer; evaluating pairs serially", raw_workers
                )
                workers = 1
        return cls(
            workers=workers,
            check_ast_immutable=bool(env.get("TOWEL_CHECK_AST_IMMUTABLE")),
            debug_rejections=bool(env.get("DEBUG_PROPOSAL_REJECTIONS")),
            debug_validation=bool(env.get("DEBUG_VALIDATION")),
            debug_overlap=bool(env.get("DEBUG_OVERLAP_FILTER")),
            debug_types=bool(env.get("TOWEL_DEBUG_TYPES")),
        )

    def enable_debug_logging(self) -> None:
        """Turn on the debug loggers the environment asked for; leave the others as configured."""
        for enabled, logger in (
            (self.debug_rejections, REJECTIONS),
            (self.debug_validation, VALIDATION),
            (self.debug_overlap, OVERLAP),
            (self.debug_types, TYPES),
        ):
            if enabled:
                logger.setLevel(logging.DEBUG)


class _CurrentStderrHandler(logging.StreamHandler[TextIO]):
    """A stream handler that writes to whatever ``sys.stderr`` is when a record is emitted.

    Binding the stream object once would send every later message to the
    first stderr seen, which is wrong as soon as anything redirects it.
    """

    @property
    def stream(self) -> TextIO:
        return sys.stderr

    @stream.setter
    def stream(self, value: object) -> None:
        pass


def configure_stderr_logging(level: int = logging.INFO) -> None:
    """Send the ``towel`` loggers to stderr as bare messages; the command line calls this once.

    The logger keeps propagating: the root logger has no handlers on the
    command line, so nothing prints twice, and a capturing handler on the
    root still sees the records.
    """
    if any(isinstance(handler, _CurrentStderrHandler) for handler in LOG.handlers):
        return
    handler = _CurrentStderrHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    LOG.addHandler(handler)
    LOG.setLevel(level)
