# Requirements

[Documentation index](README.md)

```bash
uv tool install code-towel     # or: pipx install code-towel
towel --version
```

Towel needs Python and nothing else: its runtime uses only the standard
library. The optional `format` and `types` extras add Ruff, Black, mypy and
Pyright, which Towel uses when a project configures them.

## Requirements

Python 3.11 to 3.15 on a POSIX system (macOS or Linux). Applying changes needs POSIX filesystem semantics. Parallel analysis uses the `fork` start method; under other start methods analysis runs on a single core. Windows is not a supported or validated release platform.

Towel installs on newer Pythons too, and warns that they are outside the
tested range. Every change to Towel runs its full test suite on Linux with each
supported Python, and a narrower set on macOS.

**CPU.** One core is enough. For a large project Towel forks workers, at most
eight and no more than the cores and memory allow; they speed the analysis up
and never change its result. `--jobs N` or `TOWEL_WORKERS=N` sets the number.

**Memory.** Tens of megabytes for a module, about 250 MB for a 140,000-line
project in one process, more with several workers
([performance](PERFORMANCE.md#memory)). Measured on an Apple M5 Max (18 cores,
128 GiB) before 2.0; the
[measurement environment](design/CORRECTNESS_MODEL.md#measurement-environment)
records the rest.

**Disk.** A run copies the project's Python sources and configuration into a
private temporary directory, removed when it ends. Writing to a new output
copies the whole target there. Type checking keeps a second copy of the
checker's inputs, and a mypy cache, for the length of the run. An interrupted
in-place write keeps the original files in its journal until you recover them
([recovery](CLI_GUIDE.md#recovery-and-interrupted-writes)).
