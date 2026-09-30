# Published 1.772 comparison evidence

These immutable observations compare the actual published wheel with frozen
candidate `3af8c4b`. They are historical facts, not expectations that later
versions must reproduce a speedup. Preserve them; a new measurement belongs in
separately identified evidence. The documentary regressions protect the intended
meaning as well as the numerical claims.

`provenance.json` records every retained original byte length and SHA-256;
`.gz` files decompress to the exact corresponding source records. Source paths
are relative to the external archive named in the manifest. The full archive
is `~/Knowledge/handoffs/towel/20260927T181305Z-codex-next-validation/performance-vs-1772`.
Original Sphinx source bodies remain there; retained request fingerprints replace
only those bodies with their verified lengths and SHA-256 hashes. The retention
script independently reconciled every fingerprint against its original request.

The Packaging timing covers the complete CLI; its overlapping samples establish
no meaningful speedup. Candidate and published extraction counts differ. The
Sphinx timing covers only its first Pyright reveal, with identical requests and
answers across four alternating runs. It establishes no full-run or whole-corpus
speedup. Two samples per version do not establish a confidence interval. Heavy
validation had completed, but ordinary desktop activity continued; this was not
a dedicated idle machine. The raw preflight and process records preserve that
limitation. Earlier stopped benchmark attempts remain explicitly distinct.

Both artifacts report version 1.772; hashes distinguish them. The candidate is
validation only and must not be uploaded as 1.772.
