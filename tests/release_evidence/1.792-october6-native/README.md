# October 6 native validation

Source checkpoint `1e87842e9ac50952e74643594cd41dbd258203ef`, runtime tree
`87bd254a7948b5611b334e85856c2aec94d13207`. The installed-wheel performance
checkpoint `8626d50` has identical runtime bytes. Native Python 3.13.7 on macOS.

Complete pytest under coverage: **9,671 passed, 20 skipped, 26 subtests passed**,
4 warnings, 1500.22 seconds. All skips and warnings remain in the raw pytest log.
Combined multiprocessing/thread coverage: **93%**, above the declared 85% gate
(28,984 statements; 1,939 uncovered). Black, Flake8, mypy and Bandit all passed.

`result.json` binds interpreter, source import, runtime tree, command arguments
and exits. Before/after full non-ignored source manifests match exactly; the
validation run changed no tracked source, test, fixture, documentation or
evidence bytes. `validation-source.py.txt` is the runner. Gzip files preserve
original raw logs/manifests; no successful result was inferred from partial logs.

No matrix or corpus was run. This native checkpoint does not clear the release
gates requiring those runs and source-bound artifact/remote-CI validation.
