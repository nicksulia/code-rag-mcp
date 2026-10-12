"""
REST API and Web Server for Multi-Repository Code RAG.
Provides JSON API endpoints, SSE streaming RAG responses, and static asset serving for Web UI.
"""

import os
import json
import logging
import secrets
import ipaddress
import socket
import re
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from pathlib import Path
from typing import Dict, Any, Optional

from ..service import MultiRepoRAGService, ReindexInProgressError
from ..mcp.server import MCPServer
from ..ingestion.repo_manager import (
    RelationError,
    UnknownRepositoryError,
    UnknownGroupError,
    DuplicateGroupError,
    SelfDependencyError,
    DependencyCycleError,
    PROJECT_REQUEST_LIMIT,
    TransferValidationError,
    TransferConflictError,
    RepositoryOperationError,
    safe_operation_error,
    validate_sync_options,
)

logger = logging.getLogger("rag.api")


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise TransferValidationError("JSON contains a duplicate object key.")
        result[key] = value
    return result


def _reject_json_constant(value):
    raise TransferValidationError("JSON contains an invalid numeric constant.")


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


_LOCALHOST_NAMES = frozenset({"localhost", "127.0.0.1", "::1"})
_WILDCARD_BIND_HOSTS = frozenset({"0.0.0.0", "::", ""})
_LOCAL_AUTHORITY = re.compile(r"(?:localhost|[0-9.]+|\[[0-9a-f:.]+\])(?::[0-9]+)?", re.IGNORECASE)


def listener_is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        try:
            addresses = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except socket.gaierror:
            raise RepositoryOperationError("ssh_http_forbidden", "http_policy") from None
        return bool(addresses) and all(ipaddress.ip_address(item[4][0]).is_loopback for item in addresses)


def make_request_handler(
    service: MultiRepoRAGService,
    web_dir: Path,
    bind_host: str = "127.0.0.1",
    mcp_token: Optional[str] = None,
):
    service.repo_manager.ssh_network_allowed = listener_is_loopback(bind_host)
    mcp_server = MCPServer(service=service)

    # Origin hostnames accepted on /mcp: localhost variants plus the configured
    # bind host when it is a concrete non-localhost address.
    mcp_allowed_origin_hosts = set(_LOCALHOST_NAMES)
    if bind_host not in _LOCALHOST_NAMES and bind_host not in _WILDCARD_BIND_HOSTS:
        mcp_allowed_origin_hosts.add(bind_host.lower())

    class RAGAPIRequestHandler(BaseHTTPRequestHandler):
        def _loopback(self):
            return listener_is_loopback(self.server.server_address[0])

        def _header_values(self, name):
            if hasattr(self.headers, "get_all"):
                return self.headers.get_all(name, [])
            value = self.headers.get(name)
            return [] if value is None else [value]

        def _request_allowed(self):
            if not self._loopback():
                return True
            try:
                hosts = self._header_values("Host")
                if len(hosts) != 1 or not _LOCAL_AUTHORITY.fullmatch(hosts[0]):
                    raise ValueError
                authority = urllib.parse.urlsplit("//" + hosts[0])
                hostname = authority.hostname
                port = authority.port if authority.port is not None else 80
                if not hostname or authority.username or authority.password:
                    raise ValueError
                if hostname.lower() != "localhost":
                    if not ipaddress.ip_address(hostname).is_loopback:
                        raise ValueError
                if port != self.server.server_address[1]:
                    raise ValueError
                origins = self._header_values("Origin")
                if origins:
                    if len(origins) != 1 or re.search(r"[\s,\\]", origins[0]):
                        raise ValueError
                    origin = urllib.parse.urlsplit(origins[0])
                    if (origin.scheme != "http" or origin.hostname != hostname
                            or not _LOCAL_AUTHORITY.fullmatch(origin.netloc)
                            or (origin.port if origin.port is not None else 80) != port or origin.path
                            or origin.query or origin.fragment or origin.username is not None
                            or origin.password is not None or origins[0].endswith(("?", "#"))):
                        raise ValueError
                fetch = self._header_values("Sec-Fetch-Site")
                if fetch and (len(fetch) != 1 or fetch[0] not in {"same-origin", "none"}):
                    raise ValueError
                return True
            except ValueError:
                self._send_error(403, "Forbidden request provenance")
                return False

        def log_message(self, format, *args):
            # Suppress normal access logs to keep console clean
            pass

        # --- MCP endpoint (/mcp) -----------------------------------------

        def _send_mcp_json(
            self,
            status_code: int,
            data: Any,
            extra_headers: Optional[Dict[str, str]] = None,
        ):
            # Deliberately no Access-Control-Allow-Origin here: MCP responses
            # must not be readable cross-origin by browsers.
            body = json.dumps(data, indent=2).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            for key, value in (extra_headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def _send_mcp_empty(self, status_code: int):
            self.send_response(status_code)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _mcp_origin_allowed(self) -> bool:
            if self._loopback():
                return True
            origin = self.headers.get("Origin")
            if not origin:
                # Non-browser clients do not send Origin.
                return True
            hostname = urllib.parse.urlparse(origin).hostname
            if not hostname:
                return False
            return hostname.lower() in mcp_allowed_origin_hosts

        def _mcp_auth_authorized(self) -> bool:
            if not mcp_token:
                return True
            header = self.headers.get("Authorization") or ""
            return secrets.compare_digest(header, f"Bearer {mcp_token}")

        def _handle_mcp_post(self):
            if not self._mcp_origin_allowed():
                self._send_mcp_json(403, {"error": "Forbidden origin", "status": 403})
                return
            if not self._mcp_auth_authorized():
                self._send_mcp_json(
                    401,
                    {"error": "Unauthorized", "status": 401},
                    extra_headers={"WWW-Authenticate": "Bearer"},
                )
                return

            content_len = int(self.headers.get("Content-Length", 0))
            raw = (
                self.rfile.read(content_len).decode("utf-8") if content_len > 0 else ""
            )
            try:
                message = json.loads(raw)
            except ValueError:
                self._send_mcp_json(
                    400,
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32700, "message": "Parse error"},
                    },
                )
                return

            if isinstance(message, list):
                # JSON-RPC batching was removed from the MCP spec in 2025-03-26.
                self._send_mcp_json(
                    400,
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {
                            "code": -32600,
                            "message": "JSON-RPC batch requests are not supported",
                        },
                    },
                )
                return

            if not isinstance(message, dict):
                self._send_mcp_json(
                    400,
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32600, "message": "Invalid Request"},
                    },
                )
                return

            if "method" not in message:
                # JSON-RPC response or malformed notification: nothing to answer.
                self._send_mcp_empty(202)
                return

            try:
                response = mcp_server.handle_request(message)
            except Exception as e:
                self._send_mcp_json(
                    500,
                    {
                        "jsonrpc": "2.0",
                        "id": message.get("id"),
                        "error": {"code": -32603, "message": f"Internal error: {e}"},
                    },
                )
                return

            if response is None:
                self._send_mcp_empty(202)
            else:
                self._send_mcp_json(200, response)

        def _send_mcp_method_not_allowed(self):
            self._send_mcp_json(
                405,
                {"error": "Method Not Allowed", "status": 405},
                extra_headers={"Allow": "POST"},
            )

        # --- REST API -----------------------------------------------------

        def _send_json(self, status_code: int, data: Any):
            body = json.dumps(data, indent=2).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            if not self._loopback():
                self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header(
                "Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS"
            )
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()
            self.wfile.write(body)

        def _send_error(self, status_code: int, message: str):
            self._send_json(status_code, {"error": message, "status": status_code})

        def _handle_project_export(self):
            try:
                body = service.export_projects()
            except (TransferValidationError, RelationError) as error:
                self._send_error(400, str(error))
                return
            except Exception as error:
                # Exception values may contain source addresses or credentials.
                logger.error("Project export failed (%s).", type(error).__name__)
                self._send_error(500, "Project export failed due to a catalog error.")
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header(
                "Content-Disposition", 'attachment; filename="projects.json"'
            )
            if not self._loopback():
                self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def _handle_project_import(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except (TypeError, ValueError):
                self._send_error(400, "Content-Length must be a nonnegative integer.")
                return
            if length > PROJECT_REQUEST_LIMIT:
                self.close_connection = True
                self._send_error(
                    413,
                    f"Import request exceeds the {PROJECT_REQUEST_LIMIT}-byte limit.",
                )
                return
            if length <= 0:
                self._send_error(400, "Import requires a nonempty JSON request body.")
                return
            try:
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise TransferValidationError("Import request body is incomplete.")
                body = json.loads(
                    raw.decode("utf-8"),
                    object_pairs_hook=_unique_json_object,
                    parse_constant=_reject_json_constant,
                )
                if (
                    not isinstance(body, dict)
                    or "document" not in body
                    or set(body) - {"document", "dry_run"}
                ):
                    raise TransferValidationError(
                        "Import requires document and optional dry_run; no other fields are allowed."
                    )
                dry_run = body.get("dry_run", True)
                if not isinstance(dry_run, bool):
                    raise TransferValidationError("dry_run must be a boolean.")
                if dry_run:
                    summary = service.preview_project_import(body["document"])
                    response = {"dry_run": True, **summary}
                else:
                    summary = service.import_projects(body["document"])
                    response = {"dry_run": False, "success": True, **summary}
            except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
                self._send_error(400, "Import body must be valid UTF-8 JSON.")
                return
            except (TransferValidationError, RelationError) as error:
                self._send_error(400, str(error))
                return
            except TransferConflictError as error:
                self._send_error(409, str(error))
                return
            except Exception as error:
                logger.error("Project import failed (%s).", type(error).__name__)
                self._send_error(
                    500, "Project import failed due to a catalog or persistence error."
                )
                return
            self._send_json(200, response)

        def _send_repository_error(self, error, repo_id=None, phase="index", configuration_operation=False):
            safe = safe_operation_error(error, repo_id, phase)
            self._send_json(safe.http_status, {
                **safe.to_dict(include_config_saved=configuration_operation),
                "status": safe.http_status,
            })

        def _read_strict_json(self, limit, code):
            try:
                lengths = self._header_values("Content-Length")
                if len(lengths) != 1 or not re.fullmatch(r"[0-9]+", lengths[0]):
                    raise ValueError
                length = int(lengths[0])
                if length > limit:
                    self.close_connection = True
                    raise RepositoryOperationError("ssh_key_body_too_large", "ssh_validation")
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise ValueError
                body = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_json_object,
                                  parse_constant=_reject_json_constant) if raw else {}
                if not isinstance(body, dict):
                    raise ValueError
                return body
            except (ValueError, TypeError, UnicodeError, RecursionError):
                phase = "ssh_validation" if code == "ssh_key_invalid" else "save_configuration"
                raise RepositoryOperationError(code, phase) from None

        def _key_request(self, method, key_id=None):
            try:
                service.repo_manager.require_ssh_management()
                manager = service.repo_manager
                if method == "GET":
                    result, status = {"ssh_keys": manager.list_ssh_keys()}, 200
                elif method == "DELETE":
                    manager.delete_ssh_key(key_id)
                    result, status = {"success": True, "deleted_key_id": key_id}, 200
                else:
                    body = self._read_strict_json(8192, "ssh_key_invalid")
                    required = {"name", "private_key_path"} if method == "POST" else {"name"}
                    if set(body) != required or any(not isinstance(body[field], str) for field in required):
                        raise RepositoryOperationError("ssh_key_invalid", "ssh_validation")
                    profile = (service.register_ssh_key(body["name"], body["private_key_path"])
                               if method == "POST" else manager.rename_ssh_key(key_id, body["name"]))
                    result, status = {"ssh_key": profile}, 201 if method == "POST" else 200
            except Exception as error:
                self._send_repository_error(error, phase="ssh_catalog")
                return
            self._send_json(status, result)

        def _repository_configuration(self, method, repo_id=None):
            try:
                body = self._read_strict_json(8192, "ssh_assignment_invalid")
                fields = {"url_or_path", "branch", "name", "auto_sync", "ssh_key_id", "repo_id", "source_type"}
                if set(body) - fields:
                    raise RepositoryOperationError("ssh_assignment_invalid", "save_configuration")
                for field in ("repo_id", "source_type", "url_or_path", "name", "branch"):
                    if field in body and body[field] is not None and not isinstance(body[field], str):
                        raise RepositoryOperationError("ssh_assignment_invalid", "save_configuration")
                if not isinstance(body.get("auto_sync", True), bool):
                    raise RepositoryOperationError("ssh_assignment_invalid", "save_configuration")
                if "ssh_key_id" in body and body["ssh_key_id"] is not None and not isinstance(body["ssh_key_id"], str):
                    raise RepositoryOperationError("ssh_assignment_invalid", "save_configuration")
                assignment = {"ssh_key_id": body["ssh_key_id"]} if "ssh_key_id" in body else None
                if method == "POST":
                    repo_id = body.get("repo_id")
                    if not repo_id or not body.get("url_or_path") or body.get("source_type", "local") not in {"git", "local"}:
                        raise RepositoryOperationError("ssh_assignment_invalid", "save_configuration")
                    repo = service.add_repository(
                        repo_id, body.get("name") or repo_id, body.get("source_type", "local"),
                        body["url_or_path"], body.get("branch", "main"), body.get("auto_sync", True),
                        assignment=assignment,
                    )
                    result, status = {"repository": repo.to_dict()}, 201
                else:
                    result = service.update_repository(
                        repo_id=repo_id, url_or_path=body.get("url_or_path"), branch=body.get("branch"),
                        name=body.get("name"), auto_sync=body.get("auto_sync", True), assignment=assignment,
                    )
                    status = 200
            except Exception as error:
                if isinstance(error, RepositoryOperationError) and error.config_saved is None:
                    error.config_saved = False
                self._send_repository_error(error, repo_id, "save_configuration", configuration_operation=True)
                return
            self._send_json(status, result)

        def _handle_repository_sync(self, repo_id):
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length > 4096:
                    self.close_connection = True
                    safe = RepositoryOperationError("invalid_sync_options", repo_id=repo_id)
                    self._send_json(413, {**safe.to_dict(), "status": 413})
                    return
                if length < 0:
                    raise RepositoryOperationError("invalid_sync_options", repo_id=repo_id)
                raw = self.rfile.read(length) if length else b""
                if len(raw) != length:
                    raise RepositoryOperationError("invalid_sync_options", repo_id=repo_id)
                body = json.loads(
                    raw.decode("utf-8"), object_pairs_hook=_unique_json_object,
                    parse_constant=_reject_json_constant,
                ) if raw else {}
                if not isinstance(body, dict) or set(body) - {"force", "local_only"}:
                    raise RepositoryOperationError("invalid_sync_options", repo_id=repo_id)
                force, local_only = body.get("force", False), body.get("local_only", False)
                validate_sync_options(force, local_only)
            except (ValueError, TypeError, UnicodeDecodeError, RecursionError):
                self._send_repository_error(RepositoryOperationError("invalid_sync_options", repo_id=repo_id))
                return
            except RepositoryOperationError as error:
                self._send_repository_error(error)
                return
            try:
                result = service.sync_repository(repo_id, force=force, local_only=local_only)
            except Exception as error:
                self._send_repository_error(error, repo_id)
                return
            self._send_json(200, result)

        def do_OPTIONS(self):
            if not self._request_allowed():
                return
            self.send_response(200)
            if not self._loopback():
                self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header(
                "Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS"
            )
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def do_HEAD(self):
            if self._request_allowed():
                self._send_error(405, "Method Not Allowed")

        def do_GET(self):
            if not self._request_allowed():
                return
            parsed_url = urllib.parse.urlparse(self.path)
            path = parsed_url.path
            query_params = urllib.parse.parse_qs(parsed_url.query)
            if path == "/api/v1/ssh-keys":
                self._key_request("GET")
                return

            if path == "/mcp":
                # No SSE stream is offered in this iteration.
                self._send_mcp_method_not_allowed()
                return

            # API Routes
            if path == "/api/v1/projects/export":
                self._handle_project_export()
                return

            elif path == "/api/v1/repos":
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
            path = urllib.parse.unquote(path)
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
            if not self._request_allowed():
                return
            parsed_url = urllib.parse.urlparse(self.path)
            path = parsed_url.path
            if path == "/api/v1/ssh-keys":
                self._key_request("POST")
                return
            if path == "/api/v1/repos":
                self._repository_configuration("POST")
                return

            if path == "/mcp":
                self._handle_mcp_post()
                return

            if path == "/api/v1/projects/import":
                self._handle_project_import()
                return

            if path.startswith("/api/v1/repos/") and path.endswith("/sync"):
                repo_id = urllib.parse.unquote(path[len("/api/v1/repos/"):-len("/sync")])
                self._handle_repository_sync(repo_id)
                return

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

            if path == "/api/v1/groups":
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
                group_name = urllib.parse.unquote(
                    path[len("/api/v1/groups/"):-len("/members")]
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
            if not self._request_allowed():
                return
            parsed_url = urllib.parse.urlparse(self.path)
            path = parsed_url.path
            if path.startswith("/api/v1/ssh-keys/"):
                self._key_request("PUT", urllib.parse.unquote(path[len("/api/v1/ssh-keys/"):]))
                return
            if path.startswith("/api/v1/repos/"):
                self._repository_configuration("PUT", urllib.parse.unquote(path[len("/api/v1/repos/"):].strip("/")))
                return

            self._send_error(404, "Endpoint Not Found")

        def do_DELETE(self):
            if not self._request_allowed():
                return
            parsed_url = urllib.parse.urlparse(self.path)
            path = parsed_url.path
            if path.startswith("/api/v1/ssh-keys/"):
                self._key_request("DELETE", urllib.parse.unquote(path[len("/api/v1/ssh-keys/"):]))
                return

            if path == "/mcp":
                # No sessions exist to terminate in this iteration.
                self._send_mcp_method_not_allowed()
                return

            # DELETE /api/v1/groups/{name}/members/{repo_id}
            if path.startswith("/api/v1/groups/") and "/members/" in path:
                parts = path[len("/api/v1/groups/"):].split("/members/", 1)
                if len(parts) == 2:
                    group_name, member_repo_id = map(urllib.parse.unquote, parts)
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
                group_name = urllib.parse.unquote(path[len("/api/v1/groups/"):])
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
                repo_id = urllib.parse.unquote(path[len("/api/v1/repos/"):].strip("/"))
                ok = service.delete_repository(repo_id)
                if ok:
                    self._send_json(200, {"success": True, "deleted_repo_id": repo_id})
                else:
                    self._send_error(404, f"Repository {repo_id} not found")
                return

            self._send_error(404, "Endpoint Not Found")

        def _serve_static_file(self, file_path: Path, content_type: str):
            try:
                target = file_path.resolve()
                target.relative_to(web_dir.resolve())
            except (OSError, ValueError, RuntimeError):
                self._send_error(404, "File Not Found")
                return
            if not target.is_file() or service.repo_manager.protected_files().contains(target):
                self._send_error(404, "File Not Found")
                return
            with open(target, "rb") as f:
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
    mcp_token: Optional[str] = None,
):
    if web_dir is None:
        web_dir = Path(__file__).resolve().parent.parent.parent / "web"
    loopback = listener_is_loopback(host)
    if not loopback and service.repo_manager.has_ssh_keys():
        raise RepositoryOperationError("ssh_http_forbidden", "http_policy")

    handler = make_request_handler(
        service, web_dir, bind_host=host, mcp_token=mcp_token
    )
    server_type = ThreadedHTTPServer
    if ":" in host:
        class IPv6HTTPServer(ThreadedHTTPServer):
            address_family = socket.AF_INET6
        server_type = IPv6HTTPServer
    server = server_type((host, port), handler, bind_and_activate=False)
    try:
        server.server_bind()
        actual_loopback = listener_is_loopback(server.server_address[0])
        if not actual_loopback and service.repo_manager.has_ssh_keys():
            raise RepositoryOperationError("ssh_http_forbidden", "http_policy")
        service.repo_manager.ssh_network_allowed = actual_loopback
        server.server_activate()
    except BaseException:
        server.server_close()
        raise
    bound_host, bound_port = server.server_address[:2]
    authority = f"[{bound_host}]" if ":" in bound_host else bound_host
    print(f"🚀 Multi-Repository Code RAG server listening on http://{authority}:{bound_port}")
    try:
        server.serve_forever()
    except (KeyboardInterrupt, SystemExit):
        print("\n🛑 Stopping server...")
    finally:
        print("🧹 Releasing models and instance lock on exit...")
        service.shutdown()
        server.server_close()
        print("✅ Server stopped and model memory released.")
