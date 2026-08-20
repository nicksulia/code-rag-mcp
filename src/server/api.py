"""
REST API and Web Server for Multi-Repository Code RAG.
Provides JSON API endpoints, SSE streaming RAG responses, and static asset serving for Web UI.
"""

import os
import json
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from pathlib import Path
from typing import Dict, Any, Optional

from ..service import MultiRepoRAGService, ReindexInProgressError
from ..ingestion.repo_manager import (
    RelationError,
    UnknownRepositoryError,
    UnknownGroupError,
    DuplicateGroupError,
    SelfDependencyError,
    DependencyCycleError,
)


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def make_request_handler(service: MultiRepoRAGService, web_dir: Path):
    class RAGAPIRequestHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            # Suppress normal access logs to keep console clean
            pass

        def _send_json(self, status_code: int, data: Any):
            body = json.dumps(data, indent=2).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header(
                "Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS"
            )
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()
            self.wfile.write(body)

        def _send_error(self, status_code: int, message: str):
            self._send_json(status_code, {"error": message, "status": status_code})

        def do_OPTIONS(self):
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header(
                "Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS"
            )
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def do_GET(self):
            parsed_url = urllib.parse.urlparse(self.path)
            path = parsed_url.path
            query_params = urllib.parse.parse_qs(parsed_url.query)

            # API Routes
            if path == "/api/v1/repos":
                repos = service.list_repositories()
                self._send_json(200, {"repositories": [r.to_dict() for r in repos]})
                return

            elif path == "/api/v1/groups":
                groups = service.repo_manager.list_groups()
                self._send_json(200, {"groups": [g.to_dict() for g in groups]})
                return

            elif path.startswith("/api/v1/repos/") and path.endswith("/relations"):
                repo_id = (
                    path.replace("/api/v1/repos/", "")
                    .replace("/relations", "")
                    .strip("/")
                )
                try:
                    relations = service.repo_manager.get_repo_relations(repo_id)
                    self._send_json(200, relations)
                except UnknownRepositoryError as e:
                    self._send_error(404, str(e))
                except Exception as e:
                    self._send_error(400, str(e))
                return

            elif path.startswith("/api/v1/symbols/"):
                symbol_name = urllib.parse.unquote(path.replace("/api/v1/symbols/", ""))
                repo_id = query_params.get("repo_id", [None])[0]
                info = service.get_symbol_info(symbol_name, repo_id)
                self._send_json(200, info)
                return

            elif path == "/api/v1/models/status":
                self._send_json(200, service.embedding_runtime_state())
                return

            elif path == "/api/v1/graph/cross-repo":
                links = service.get_cross_repo_api_links()
                self._send_json(200, {"cross_repo_links": links})
                return

            elif path == "/api/v1/file":
                repo_id = query_params.get("repo_id", [""])[0]
                file_path = query_params.get("path", [""])[0]
                if not repo_id or not file_path:
                    self._send_error(400, "Missing repo_id or path parameter")
                    return
                content = service.get_file_content(repo_id, file_path)
                if content is None:
                    self._send_error(404, "File not found")
                    return
                self._send_json(
                    200,
                    {"repo_id": repo_id, "file_path": file_path, "content": content},
                )
                return

            # Static Web UI serving
            if path == "/" or path == "/index.html":
                self._serve_static_file(web_dir / "index.html", "text/html")
            elif path.endswith(".css"):
                self._serve_static_file(web_dir / path.lstrip("/"), "text/css")
            elif path.endswith(".js"):
                self._serve_static_file(
                    web_dir / path.lstrip("/"), "application/javascript"
                )
            else:
                target = web_dir / path.lstrip("/")
                if target.exists() and target.is_file():
                    self._serve_static_file(target, "application/octet-stream")
                else:
                    self._send_error(404, "Page Not Found")

        def do_POST(self):
            parsed_url = urllib.parse.urlparse(self.path)
            path = parsed_url.path

            content_len = int(self.headers.get("Content-Length", 0))
            post_data = (
                self.rfile.read(content_len).decode("utf-8")
                if content_len > 0
                else "{}"
            )
            try:
                body = json.loads(post_data) if post_data else {}
            except Exception:
                body = {}

            if path == "/api/v1/repos":
                repo_id = body.get("repo_id")
                name = body.get("name") or repo_id
                source_type = body.get("source_type", "local")
                url_or_path = body.get("url_or_path")
                branch = body.get("branch", "main")
                auto_sync = body.get("auto_sync", True)

                if not repo_id or not url_or_path:
                    self._send_error(
                        400, "Missing required fields: repo_id, url_or_path"
                    )
                    return

                try:
                    repo = service.add_repository(
                        repo_id, name, source_type, url_or_path, branch, auto_sync
                    )
                    self._send_json(201, {"repository": repo.to_dict()})
                except Exception as e:
                    self._send_error(500, str(e))
                return

            elif path == "/api/v1/groups":
                name = body.get("name")
                repo_ids = body.get("repo_ids", [])
                if not name:
                    self._send_error(400, "Missing required field: name")
                    return
                try:
                    group = service.repo_manager.create_group(name, repo_ids)
                    self._send_json(201, {"group": group.to_dict()})
                except (DuplicateGroupError, UnknownRepositoryError) as e:
                    self._send_error(400, str(e))
                except Exception as e:
                    self._send_error(400, str(e))
                return

            elif path.startswith("/api/v1/groups/") and path.endswith("/members"):
                group_name = (
                    path.replace("/api/v1/groups/", "")
                    .replace("/members", "")
                    .strip("/")
                )
                repo_ids = body.get("repo_ids", [])
                if not isinstance(repo_ids, list):
                    self._send_error(400, "repo_ids must be a list")
                    return
                try:
                    group = service.repo_manager.add_repos_to_group(
                        group_name, repo_ids
                    )
                    self._send_json(200, {"group": group.to_dict()})
                except (UnknownGroupError, UnknownRepositoryError) as e:
                    self._send_error(400, str(e))
                except Exception as e:
                    self._send_error(400, str(e))
                return

            elif path.startswith("/api/v1/repos/") and path.endswith("/dependencies"):
                repo_id = (
                    path.replace("/api/v1/repos/", "")
                    .replace("/dependencies", "")
                    .strip("/")
                )
                target = body.get("depends_on") or body.get("target_repo_id")
                if not target:
                    self._send_error(400, "Missing required field: depends_on")
                    return
                try:
                    dep = service.repo_manager.add_dependency(repo_id, target)
                    self._send_json(201, {"dependency": dep.to_dict()})
                except (
                    SelfDependencyError,
                    DependencyCycleError,
                    UnknownRepositoryError,
                ) as e:
                    self._send_error(400, str(e))
                except Exception as e:
                    self._send_error(400, str(e))
                return

            elif path.endswith("/sync") and path.startswith("/api/v1/repos/"):
                repo_id = (
                    path.replace("/api/v1/repos/", "").replace("/sync", "").strip("/")
                )
                try:
                    res = service.sync_repository(repo_id)
                    self._send_json(200, res)
                except Exception as e:
                    self._send_error(500, str(e))
                return

            elif path == "/api/v1/search":
                query = body.get("query", "")
                repo_ids = body.get("repo_ids")
                groups = body.get("groups")
                expand = body.get("expand", "none")
                expand_depth = body.get("expand_depth", 1)
                top_k = body.get("top_k", 8)
                try:
                    scope = service.resolve_scope(
                        repo_ids=repo_ids,
                        groups=groups,
                        expand=expand,
                        expand_depth=expand_depth,
                    )
                    results = service.search(
                        query=query,
                        repo_ids=repo_ids,
                        groups=groups,
                        expand=expand,
                        expand_depth=expand_depth,
                        top_k=top_k,
                    )
                    res_obj = {
                        "query": query,
                        "results": [r.to_dict() for r in results],
                    }
                    if groups is not None or (expand is not None and expand != "none"):
                        res_obj["scope"] = scope.to_dict()
                    self._send_json(200, res_obj)
                except ReindexInProgressError as e:
                    self._send_json(
                        503,
                        {
                            "error": str(e),
                            "status": 503,
                            "reindexing": True,
                        },
                    )
                except (UnknownGroupError, UnknownRepositoryError) as e:
                    self._send_error(400, str(e))
                except Exception as e:
                    self._send_error(500, str(e))
                return

            elif path == "/api/v1/models/unload":
                res = service.unload_models()
                if res.get("busy"):
                    self._send_json(
                        409,
                        {
                            "success": False,
                            "busy": True,
                            "active_operations": res.get("active_operations", 0),
                            "error": "Embedding model is busy; unload skipped.",
                            "unloaded": res,
                        },
                    )
                    return
                self._send_json(200, {"success": True, "unloaded": res})
                return

            self._send_error(404, "Endpoint Not Found")

        def do_PUT(self):
            parsed_url = urllib.parse.urlparse(self.path)
            path = parsed_url.path

            content_len = int(self.headers.get("Content-Length", 0))
            post_data = (
                self.rfile.read(content_len).decode("utf-8")
                if content_len > 0
                else "{}"
            )
            try:
                body = json.loads(post_data) if post_data else {}
            except Exception:
                body = {}

            if path.startswith("/api/v1/repos/"):
                repo_id = path.replace("/api/v1/repos/", "").strip("/")
                url_or_path = body.get("url_or_path")
                branch = body.get("branch")
                name = body.get("name")
                auto_sync = body.get("auto_sync", True)

                try:
                    result = service.update_repository(
                        repo_id=repo_id,
                        url_or_path=url_or_path,
                        branch=branch,
                        name=name,
                        auto_sync=auto_sync,
                    )
                    self._send_json(200, result)
                except Exception as e:
                    self._send_error(500, str(e))
                return

            self._send_error(404, "Endpoint Not Found")

        def do_DELETE(self):
            parsed_url = urllib.parse.urlparse(self.path)
            path = parsed_url.path

            # DELETE /api/v1/groups/{name}/members/{repo_id}
            if path.startswith("/api/v1/groups/") and "/members/" in path:
                parts = path.replace("/api/v1/groups/", "").split("/members/")
                if len(parts) == 2:
                    group_name, member_repo_id = parts[0], parts[1]
                    try:
                        ok = service.repo_manager.remove_repo_from_group(
                            group_name, member_repo_id
                        )
                        if ok:
                            self._send_json(
                                200,
                                {"success": True, "removed_repo_id": member_repo_id},
                            )
                        else:
                            self._send_error(
                                404,
                                f"Member '{member_repo_id}' not found in group '{group_name}'",
                            )
                    except UnknownGroupError as e:
                        self._send_error(404, str(e))
                    except Exception as e:
                        self._send_error(400, str(e))
                    return

            # DELETE /api/v1/groups/{name}
            elif path.startswith("/api/v1/groups/"):
                group_name = path.replace("/api/v1/groups/", "").strip("/")
                ok = service.repo_manager.delete_group(group_name)
                if ok:
                    self._send_json(200, {"success": True, "deleted_group": group_name})
                else:
                    self._send_error(404, f"Group '{group_name}' not found")
                return

            # DELETE /api/v1/repos/{repo_id}/dependencies/{target_repo_id}
            elif path.startswith("/api/v1/repos/") and "/dependencies/" in path:
                parts = path.replace("/api/v1/repos/", "").split("/dependencies/")
                if len(parts) == 2:
                    source_repo, target_repo = parts[0], parts[1]
                    ok = service.repo_manager.remove_dependency(
                        source_repo, target_repo
                    )
                    if ok:
                        self._send_json(
                            200,
                            {
                                "success": True,
                                "removed_dependency": f"{source_repo}->{target_repo}",
                            },
                        )
                    else:
                        self._send_error(
                            404,
                            f"Dependency edge '{source_repo}' -> '{target_repo}' not found",
                        )
                    return

            # DELETE /api/v1/repos/{repo_id}
            elif path.startswith("/api/v1/repos/"):
                repo_id = path.replace("/api/v1/repos/", "").strip("/")
                ok = service.delete_repository(repo_id)
                if ok:
                    self._send_json(200, {"success": True, "deleted_repo_id": repo_id})
                else:
                    self._send_error(404, f"Repository {repo_id} not found")
                return

            self._send_error(404, "Endpoint Not Found")

        def _serve_static_file(self, file_path: Path, content_type: str):
            if not file_path.exists():
                self._send_error(404, "File Not Found")
                return
            with open(file_path, "rb") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", f"{content_type}; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

    return RAGAPIRequestHandler


def start_server(
    service: MultiRepoRAGService,
    host: str = "127.0.0.1",
    port: int = 8000,
    web_dir: Optional[Path] = None,
):
    if web_dir is None:
        web_dir = Path(__file__).resolve().parent.parent.parent / "web"
    web_dir.mkdir(parents=True, exist_ok=True)

    handler = make_request_handler(service, web_dir)
    server = ThreadedHTTPServer((host, port), handler)
    print(f"🚀 Multi-Repository Code RAG server listening on http://{host}:{port}")
    try:
        server.serve_forever()
    except (KeyboardInterrupt, SystemExit):
        print("\n🛑 Stopping server...")
    finally:
        print("🧹 Releasing models and instance lock on exit...")
        service.shutdown()
        server.server_close()
        print("✅ Server stopped and model memory released.")
