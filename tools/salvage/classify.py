"""Lexical-only file classification for salvage packages.

Zero domain semantics: the rules below know about *file kinds* (source, prose,
artefact, temporary), never about any scientific field.  Five classes:

``code``     executable / applicable sources: .py .sh .sql .toml .yaml ...; also
             build manifests and config that a resuming worker must re-apply.
``doc``      prose: .md .rst .tex .bib and plain .txt notes.
``data``     produced artefacts and media: .json .csv .png .log .npz ...
``scratch``  disposable debugging residue: hidden ``.scratch``/``tmp`` style
             directories, caches (``__pycache__``), .pyc/.swp editor droppings.
             **Never packaged** (dropped and counted).
``other``    anything the rules do not recognise (kept, flagged for human triage).

The classifier is a pure function of the path, so manifests stay reproducible.
"""
from __future__ import annotations

CODE_EXT = frozenset(
    """py pyi pyx pxd c h cc cpp cxx hpp hh cu cuh f f90 f95 f03 for jl rs go
    js mjs cjs ts tsx jsx vue svelte sh bash zsh fish ps1 bat cmd sql toml yaml
    yml ini cfg conf dockerfile mk cmake gradle lean r R m mm lua pl rb php
    java kt scala swift dart cs fs ex exs erl hs ml clj sol move cairo
    proto thrift graphql gql css scss sass less html htm xml xsd wgsl glsl
    vert frag comp shader""".split()
)

DOC_EXT = frozenset("""md markdown rst tex bib org adoc asciidoc txt readme""".split())

DATA_EXT = frozenset(
    """json jsonl ndjson csv tsv png jpg jpeg gif svg pdf webp bmp tif tiff
    npz npy h5 hdf5 parquet feather pkl pickle joblib dat bin log out err db
    sqlite sqlite3 gz zip tar tgz bz2 xz 7z xlsx ods mat mtx npz2 pt pth onnx
    mp4 webm wav mp3""".split()
)

SCRATCH_EXT = frozenset(
    """pyc pyo pyd swp swo tmp temp bak orig rej patch~ aux toc out.tmp
    class o obj so dylib dll exe gch pdb ipc""".split()
)

SCRATCH_DIRS = frozenset(
    """.scratch scratch .tmp tmp temp .temp __pycache__ .pytest_cache
    .mypy_cache .ruff_cache .ipynb_checkpoints node_modules .cache .tox
    .eggs .venv venv env dist build .idea .vscode .DS_Store""".split()
)

CLASSES = ("code", "doc", "data", "scratch", "other")
KEPT_CLASSES = ("code", "doc", "data", "other")


def _ext(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    if "." not in name:
        return ""
    return name.rsplit(".", 1)[-1].lower()


def _segments(path: str) -> list[str]:
    return [seg for seg in path.split("/") if seg]


def is_scratch_path(path: str) -> tuple[bool, str]:
    """(is_scratch, reason) — purely lexical."""
    segs = _segments(path)
    for seg in segs[:-1]:
        if seg in SCRATCH_DIRS:
            return True, f"scratch_dir:{seg}"
    name = segs[-1] if segs else path
    if name in SCRATCH_DIRS:
        return True, f"scratch_dir:{name}"
    if name.startswith(".#") or name.endswith("~"):
        return True, "editor_backup"
    ext = _ext(path)
    if ext in SCRATCH_EXT:
        return True, f"scratch_ext:.{ext}"
    return False, ""


def classify(path: str) -> tuple[str, str]:
    """Return ``(class, reason)`` for a repository-relative path."""
    scratch, reason = is_scratch_path(path)
    if scratch:
        return "scratch", reason
    name = path.rsplit("/", 1)[-1]
    ext = _ext(path)
    if ext in CODE_EXT:
        return "code", f"ext:.{ext}"
    if ext in DOC_EXT:
        return "doc", f"ext:.{ext}"
    if ext in DATA_EXT:
        return "data", f"ext:.{ext}"
    lowered = name.lower()
    if lowered in {"dockerfile", "makefile", "justfile", "procfile"}:
        return "code", f"filename:{lowered}"
    if lowered.startswith("readme") or lowered.startswith("license") or lowered.startswith("changelog"):
        return "doc", "filename:prose"
    return "other", "unrecognised"


def is_drop_class(cls: str) -> bool:
    return cls == "scratch"