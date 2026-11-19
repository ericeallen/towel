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

"""``python -m towel``: the ``towel`` command."""

import os
import signal
import sys

from towel import cli
from towel.execution_budget import before_interrupt, raise_interrupt
from towel.unification.progress import open_terminal_output


def _prepare_terminal_interrupts() -> None:
    stream = sys.stderr
    if stream is not sys.__stderr__ or stream is None or not os.isatty(2):
        return
    output = open_terminal_output(stream)
    if output is None:
        return
    try:
        sink = open(os.devnull, "wb", buffering=0)
    except OSError:
        output.close()
        return

    def switch_terminal() -> None:
        # Python notices keep their best-effort terminal. Raw fd 2 writers
        # and saved buffered streams can neither block nor raise EAGAIN
        # over the cancellation. The shell keeps its original descriptor.
        os.dup2(sink.fileno(), 2)
        sys.stderr = output

    # Retain both descriptors through interpreter shutdown. The signal handler
    # can switch them even if the interrupted operation used the last free fd.
    before_interrupt.set(switch_terminal)
    if signal.getsignal(signal.SIGINT) is signal.default_int_handler:
        signal.signal(signal.SIGINT, raise_interrupt)


def main() -> None:
    """Let terminal diagnostics during cancellation finish even when the terminal stops."""
    _prepare_terminal_interrupts()
    cli.main()


if __name__ == "__main__":
    main()
