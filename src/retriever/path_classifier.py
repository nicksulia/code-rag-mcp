"""
Path-based chunk classifier.

Maps a chunk's file path to a ChunkClass (source/test/fixture/noise) using an
ordered, anchored rule table covering the test conventions of every language
the indexer supports. Classification is derived at query time, matches on
whole path segments and filename boundaries (never unanchored substrings),
and normalizes path separators to `/` so Windows-indexed repos classify
identically to POSIX.
"""

import re
from enum import Enum
from typing import List, Pattern, Tuple


class ChunkClass(Enum):
    SOURCE = "source"
    TEST = "test"
    FIXTURE = "fixture"
    NOISE = "noise"


_I = re.IGNORECASE

# Ordered most-specific-first: noise -> fixture -> test. `source` is the
# default when no rule matches. Directory segments are case-insensitive;
# CamelCase filename patterns (e.g. *Test.java, *Tests.cs) are case-sensitive
# to avoid false hits on ordinary lowercase filenames.
RULES: List[Tuple[Pattern[str], ChunkClass]] = [
    # --- noise: mocks, snapshots, Storybook stories ---
    (re.compile(r"(^|/)__mocks__/", _I), ChunkClass.NOISE),
    (re.compile(r"(^|/)mocks/", _I), ChunkClass.NOISE),
    (re.compile(r"(^|/)mock_[^/]*$", _I), ChunkClass.NOISE),
    (re.compile(r"(^|/)[^/]*_mock\.[^/]*$", _I), ChunkClass.NOISE),
    (re.compile(r"(^|/)__snapshots__/", _I), ChunkClass.NOISE),
    (re.compile(r"(^|/)[^/]*\.snap$", _I), ChunkClass.NOISE),
    (re.compile(r"(^|/)[^/]*\.stories\.[^/]*$", _I), ChunkClass.NOISE),
    # --- fixture: fixture / test-data directories ---
    (re.compile(r"(^|/)fixtures/", _I), ChunkClass.FIXTURE),
    (re.compile(r"(^|/)testdata/", _I), ChunkClass.FIXTURE),
    # --- test: directory conventions ---
    (re.compile(r"(^|/)tests?/", _I), ChunkClass.TEST),  # Python, C/C++, Rust, generic
    (re.compile(r"(^|/)__tests__/", _I), ChunkClass.TEST),  # JS/TS
    (re.compile(r"(^|/)src/test/", _I), ChunkClass.TEST),  # Java
    (re.compile(r"(^|/)benches/", _I), ChunkClass.TEST),  # Rust
    (re.compile(r"(^|/)spec/", _I), ChunkClass.TEST),  # Ruby
    (re.compile(r"(^|/)[^/]*\.Tests/"), ChunkClass.TEST),  # C# test projects
    # --- test: filename conventions ---
    (re.compile(r"(^|/)test_[^/]*\.(py|c|cc|cpp|cxx|h|hpp|sh)$"), ChunkClass.TEST),
    (re.compile(r"(^|/)[^/]*_test\.(py|go|c|cc|cpp|cxx)$"), ChunkClass.TEST),
    (re.compile(r"(^|/)[^/]*_unittest\.(c|cc|cpp|cxx)$"), ChunkClass.TEST),
    (re.compile(r"(^|/)conftest\.py$"), ChunkClass.TEST),
    (re.compile(r"(^|/)[^/]*\.(test|spec)\.[jt]sx?$"), ChunkClass.TEST),
    (re.compile(r"(^|/)[^/]*Test\.java$"), ChunkClass.TEST),
    (re.compile(r"(^|/)[^/]*Tests\.java$"), ChunkClass.TEST),
    (re.compile(r"(^|/)[^/]*IT\.java$"), ChunkClass.TEST),
    (re.compile(r"(^|/)[^/]*Tests\.cs$"), ChunkClass.TEST),
    (re.compile(r"(^|/)[^/]*_spec\.rb$"), ChunkClass.TEST),
    (re.compile(r"(^|/)[^/]*Test\.php$"), ChunkClass.TEST),
    (re.compile(r"(^|/)[^/]*\.t\.sol$"), ChunkClass.TEST),
    (re.compile(r"(^|/)[^/]*\.bats$"), ChunkClass.TEST),
    # --- forward-looking (inert until those languages are indexed) ---
    (re.compile(r"(^|/)[^/]*Test\.kts?$"), ChunkClass.TEST),  # Kotlin
    (re.compile(r"(^|/)[^/]*\.feature$"), ChunkClass.TEST),  # Gherkin
]


def classify(file_path: str) -> ChunkClass:
    """Classify a chunk's file path. Defaults to SOURCE when no rule matches."""
    if not file_path:
        return ChunkClass.SOURCE
    normalized = file_path.replace("\\", "/")
    for pattern, chunk_class in RULES:
        if pattern.search(normalized):
            return chunk_class
    return ChunkClass.SOURCE
