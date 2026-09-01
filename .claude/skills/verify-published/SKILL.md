---
name: verify-published
description: Verify a published refaudit release actually installs and runs, in a clean throwaway virtualenv. Use immediately after publishing to PyPI.
---

# Verifying a published release

A green build is not proof the artifact works. This catches the failures that
only appear on a machine that is not yours: a missing package in the wheel, a
version string that disagrees with the tag, an import that only resolved because
your editable install had the source tree on the path.

Run this in a venv with **no** `-e .` and outside the repo directory, so an
accidental import of the local source cannot mask a packaging bug.

```bash
cd $(mktemp -d)
python -m venv .venv && . .venv/bin/activate
pip install --no-cache-dir refaudit==X.Y.Z
```

`--no-cache-dir` matters: a cached wheel from a previous build is exactly the
thing you are trying not to test.

## Checks

**1. The version is the one you tagged.**
```bash
refaudit --version        # must print X.Y.Z
```
Publishing one version while the code reports another has happened here — it
turns any later bug report into a guess about what was actually running.

**2. It imports with no dependencies present.**
```bash
pip list        # refaudit and pip only; anything else means a dep leaked in
python -c "import refaudit; print(refaudit.__version__)"
```

**3. Type information ships.**
```bash
python -c "import refaudit, pathlib; print((pathlib.Path(refaudit.__file__).parent / 'py.typed').exists())"
```
Must be `True`, or downstream mypy treats the whole package as untyped.

**4. The optional extra works.**
```bash
pip install "refaudit[xml]" && python -c "import refaudit.xmlsafe as x; print(x.fromstring('<a/>'))"
```
Then confirm the fallback still works without it — that is the whole point of
the optional-dependency design.

**5. It runs end to end.** Point it at a small `.bib` with two or three entries
you know are good, with a real contact address:
```bash
refaudit sample.bib --email you@example.com --out ./out
```
Confirm it produces output, exits cleanly, and the verdicts are what you expect.
This is a live network call — keep the file small.

**6. The PyPI page renders.** Open `https://pypi.org/project/refaudit/X.Y.Z/`
and check the README displays rather than showing raw markup, and that the
classifiers and Python requirement are right.

## Clean up

```bash
deactivate && rm -rf "$PWD"
```

## If something is wrong

You cannot replace a published version. Fix forward: bump the patch, and if the
broken version is actively harmful, yank it on PyPI (which hides it from new
installs without breaking anyone who already pinned it).
