"""
Unit tests for SymbolExtractor and GraphStore.
"""

import unittest
import shutil
from pathlib import Path
from src.parser.symbol_extractor import SymbolExtractor
from src.indexer.graph_store import GraphStore


class TestSymbolGraph(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path("./test_data_graph")
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)
        self.graph_store = GraphStore(data_dir=str(self.test_dir))
        self.extractor = SymbolExtractor()

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)

    def test_extract_and_query_symbols(self):
        py_code = """
class AuthService:
    def verify_token(self, token: str):
        return True

@app.post("/api/v1/auth/login")
def login_handler():
    return AuthService().verify_token("abc")
"""
        symbols, edges = self.extractor.extract_symbols_and_edges(
            "auth-svc", "src/auth.py", py_code, "python"
        )
        self.graph_store.add_symbols_and_edges(symbols, edges)

        # Check symbol definitions
        defs = self.graph_store.get_symbol_definition("verify_token")
        self.assertTrue(len(defs) > 0)
        self.assertEqual(defs[0].repo_id, "auth-svc")

        # Check endpoints
        endpoint_defs = self.graph_store.get_symbol_definition(
            "POST /api/v1/auth/login"
        )
        self.assertTrue(len(endpoint_defs) > 0)

    def test_cross_repo_api_linkage(self):
        server_code = """
@app.post("/api/v1/auth/login")
def login(): pass
"""
        client_code = """
export async function loginUser() {
    return await apiClient.post("/api/v1/auth/login", {});
}
"""
        s_syms, s_edges = self.extractor.extract_symbols_and_edges(
            "backend-repo", "src/routes.py", server_code, "python"
        )
        c_syms, c_edges = self.extractor.extract_symbols_and_edges(
            "frontend-repo", "src/api.ts", client_code, "typescript"
        )

        self.graph_store.add_symbols_and_edges(s_syms + c_syms, s_edges + c_edges)
        links = self.graph_store.get_cross_repo_api_links()

        self.assertTrue(len(links) >= 1)
        self.assertEqual(links[0]["client_repo"], "frontend-repo")
        self.assertEqual(links[0]["server_repo"], "backend-repo")


if __name__ == "__main__":
    unittest.main()
