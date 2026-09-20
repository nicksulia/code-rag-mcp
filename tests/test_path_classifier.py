"""
Unit tests for the path-based chunk classifier.

The path -> expected-class table doubles as an executable contract for the
classification scenarios required by the retrieval-quality spec.
"""

import unittest

from src.retriever.path_classifier import ChunkClass, classify


class TestPathClassifier(unittest.TestCase):
    CASES = [
        # --- spec scenarios ---
        ("tests/test_catalog.py", ChunkClass.TEST),
        ("src/context/manager.py", ChunkClass.SOURCE),  # anchoring: "context" != test
        ("docs/latest/guide.md", ChunkClass.SOURCE),  # anchoring: "latest" != test
        ("internal/store/mocks/mock_user_store.go", ChunkClass.NOISE),
        ("tests/mocks/test_helpers.py", ChunkClass.NOISE),  # most specific wins
        ("examples/basic_usage.py", ChunkClass.SOURCE),  # examples never demoted
        ("src/parser_test.cpp", ChunkClass.TEST),
        ("foo_unittest.cc", ChunkClass.TEST),
        ("tests\\mocks\\win_helpers.py", ChunkClass.NOISE),  # Windows separators
        ("tests/fixtures/user_payload.json", ChunkClass.FIXTURE),
        ("internal/testdata/golden.json", ChunkClass.FIXTURE),
        ("__snapshots__/button.snap", ChunkClass.NOISE),
        ("src/Button.stories.tsx", ChunkClass.NOISE),
        # --- one representative convention per supported language ---
        ("catalog_test.go", ChunkClass.TEST),  # Go
        ("src/components/button.test.ts", ChunkClass.TEST),  # TS
        ("src/app.spec.js", ChunkClass.TEST),  # JS
        ("src/test/java/com/example/FooTest.java", ChunkClass.TEST),  # Java
        ("tests/UserServiceTests.cs", ChunkClass.TEST),  # C#
        ("spec/models/user_spec.rb", ChunkClass.TEST),  # Ruby
        ("tests/UserTest.php", ChunkClass.TEST),  # PHP
        ("test/Token.t.sol", ChunkClass.TEST),  # Solidity
        ("scripts/test_deploy.sh", ChunkClass.TEST),  # Bash
        ("cypress/e2e/login.feature", ChunkClass.TEST),  # Gherkin (forward-looking)
        ("tests/conftest.py", ChunkClass.TEST),  # Python conftest
        ("benches/my_bench.rs", ChunkClass.TEST),  # Rust benches
        # --- extra coverage of the rule table ---
        ("pkg/util/helper_test.py", ChunkClass.TEST),  # Python *_test.py
        ("web/__tests__/App.test.tsx", ChunkClass.TEST),  # __tests__ dir
        ("src/lib/__mocks__/axios.js", ChunkClass.NOISE),  # __mocks__ dir
        ("src/retry_mock.py", ChunkClass.NOISE),  # *_mock.* filename
        ("FooIT.java", ChunkClass.TEST),  # Java integration test suffix
        ("test_widget.bats", ChunkClass.TEST),  # Bash bats
        ("src/MainTest.kt", ChunkClass.TEST),  # Kotlin (forward-looking)
        ("src/auth.py", ChunkClass.SOURCE),  # plain source
        ("internal/store/user_store.go", ChunkClass.SOURCE),  # plain Go source
        ("src/testing_utils.py", ChunkClass.SOURCE),  # "testing" segment not matched
        ("src/mockery.py", ChunkClass.SOURCE),  # "mock" prefix alone is not mock_
    ]

    def test_path_classification(self):
        for path, expected in self.CASES:
            with self.subTest(path=path):
                self.assertEqual(classify(path), expected)

    def test_default_is_source(self):
        self.assertEqual(classify("some/random/module.xyz"), ChunkClass.SOURCE)

    def test_empty_path_is_source(self):
        self.assertEqual(classify(""), ChunkClass.SOURCE)


if __name__ == "__main__":
    unittest.main()
