import sys
import json
import logging
import traceback
from typing import Dict, Any, List, Optional
from ..service import MultiRepoRAGService, resolve_data_dir

logger = logging.getLogger("rag.mcp")


TOOLS_SPEC = [
    {
        "name": "search_codebases",
        "description": "Perform hybrid multi-vector semantic and BM25F keyword search across indexed codebases with graph boost and caller-callee relationship enrichment.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query, code concept, identifier, symbol name, or architectural keyword (e.g. 'library content catalog' or 'create_catalog').",
                },
                "repos": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of repository IDs or repository names to filter search results (e.g. ['update-api']). If omitted or null, searches across all indexed repositories.",
                },
                "groups": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of repository group names whose members join the primary search set.",
                },
                "expand": {
                    "type": "string",
                    "enum": ["none", "upstream", "downstream", "both"],
                    "description": "Optional dependency expansion direction: 'none', 'upstream' (dependencies), 'downstream' (dependents), or 'both' (default: 'none').",
                },
                "expand_depth": {
                    "type": "integer",
                    "description": "Maximum traversal depth for dependency expansion (default: 1).",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of ranked code chunks to return (default: 8, positive integer).",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_symbol_definition",
        "description": "Locate where a symbol (function, class, interface, method) is defined, its enclosing source file, line range, docstring, and implementation code.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol_name": {
                    "type": "string",
                    "description": "The exact or partial identifier of the function, class, or method definition to locate.",
                },
                "repo_id": {
                    "type": "string",
                    "description": "Optional repository ID to narrow symbol search. If omitted or null, searches across all repositories.",
                },
            },
            "required": ["symbol_name"],
        },
    },
    {
        "name": "get_call_hierarchy",
        "description": "Retrieve incoming callers and outgoing calls for a function or method across repositories via the code graph.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol_name": {
                    "type": "string",
                    "description": "The target function or method name to trace callers and callees for.",
                },
                "repo_id": {
                    "type": "string",
                    "description": "Optional repository ID to constrain caller/callee search.",
                },
            },
            "required": ["symbol_name"],
        },
    },
    {
        "name": "get_cross_repo_api_links",
        "description": "Discover cross-repository linkages where client applications invoke backend REST/API endpoints across all indexed codebases.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_repositories",
        "description": "List all registered and indexed code repositories with their file, chunk, and symbol counts, active Git branch, and sync status.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "update_repository",
        "description": "Update a repository's active Git branch (checks out and pulls), remote URL/path, or display name, and optionally re-syncs.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo_id": {
                    "type": "string",
                    "description": "The unique repository ID to update (e.g. 'update-api').",
                },
                "branch": {
                    "type": "string",
                    "description": "Target Git branch to check out and track (e.g. 'main', 'develop', 'feature/pr-51').",
                },
                "url": {
                    "type": "string",
                    "description": "New Git remote clone URL or local directory filesystem path.",
                },
                "name": {
                    "type": "string",
                    "description": "New human-readable display name for the repository.",
                },
                "auto_sync": {
                    "type": "boolean",
                    "description": "Whether to immediately check out the branch, pull, and re-index modified files (default: true).",
                },
            },
            "required": ["repo_id"],
        },
    },
    {
        "name": "sync_repository",
        "description": "Trigger synchronization and incremental indexing for a specific repository.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo_id": {
                    "type": "string",
                    "description": "The unique repository ID to synchronize.",
                },
                "force": {
                    "type": "boolean",
                    "description": "Force full re-indexing of all files ignoring cache (default: false).",
                },
            },
            "required": ["repo_id"],
        },
    },
    {
        "name": "manage_repository_relations",
        "description": "Manage repository relations including creating/deleting groups, adding/removing repositories to/from groups, and declaring or removing dependency edges between repositories.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "create_group",
                        "delete_group",
                        "add_to_group",
                        "remove_from_group",
                        "add_dependency",
                        "remove_dependency",
                    ],
                    "description": "The relation management action to perform.",
                },
                "group": {
                    "type": "string",
                    "description": "The group name (required for create_group, delete_group, add_to_group, remove_from_group).",
                },
                "repos": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Repository identifiers or names to add to or remove from a group.",
                },
                "repo": {
                    "type": "string",
                    "description": "The dependent repository identifier or name (required for add_dependency, remove_dependency).",
                },
                "depends_on": {
                    "type": "string",
                    "description": "The target repository identifier or name that is depended upon (required for add_dependency, remove_dependency).",
                },
            },
            "required": ["action"],
        },
    },
    {
        "name": "get_repository_relations",
        "description": "Inspect repository relations: reports group memberships, direct dependencies, and direct dependents for a single repository, or the entire relation graph if no repository is specified.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "Optional repository identifier or name. If omitted or null, returns all groups and all dependency edges.",
                }
            },
        },
    },
]


class MCPServer:
    def __init__(
        self, service: Optional[MultiRepoRAGService] = None, data_dir: str = "./data"
    ):
        self.service = service or MultiRepoRAGService(data_dir=data_dir)

    def handle_request(self, request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        msg_id = request.get("id")
        method = request.get("method")
        params = request.get("params", {})

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "multi-repo-code-rag", "version": "1.0.0"},
                },
            }

        elif method == "tools/list":
            return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS_SPEC}}

        elif method == "tools/call":
            tool_name = params.get("name")
            arguments = params.get("arguments")
            if not isinstance(arguments, dict):
                arguments = {}
            try:
                content = self.execute_tool(tool_name, arguments)
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [
                            {"type": "text", "text": json.dumps(content, indent=2)}
                        ]
                    },
                }
            except Exception as e:
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "isError": True,
                        "content": [
                            {
                                "type": "text",
                                "text": f"Error executing {tool_name}: {str(e)}",
                            }
                        ],
                    },
                }

        elif method == "notifications/initialized":
            return None

        else:
            if msg_id is not None:
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {
                        "code": -32601,
                        "message": f"Method '{method}' not found",
                    },
                }
            return None

    def execute_tool(self, tool_name: str, args: Dict[str, Any]) -> Any:
        """Runs a tool with the embedding model held resident for its duration."""
        with self.service.embedding_session(f"mcp:{tool_name}"):
            return self._execute_tool_impl(tool_name, args)

    def _execute_tool_impl(self, tool_name: str, args: Dict[str, Any]) -> Any:
        logger.debug(f"MCP execute_tool: name='{tool_name}', args={args}")
        if tool_name == "search_codebases":
            query = args.get("query", "")
            repos = args.get("repos")
            groups = args.get("groups")
            expand = args.get("expand", "none")
            expand_depth = args.get("expand_depth", 1)
            limit_raw = args.get("limit")
            try:
                limit = int(limit_raw) if limit_raw is not None else 8
                if limit <= 0:
                    limit = 8
            except (ValueError, TypeError):
                limit = 8
            scope = self.service.resolve_scope(
                repo_ids=repos, groups=groups, expand=expand, expand_depth=expand_depth
            )
            results = self.service.search(
                query=query,
                repo_ids=repos,
                groups=groups,
                expand=expand,
                expand_depth=expand_depth,
                top_k=limit,
            )
            logger.debug(f"MCP search_codebases: found {len(results)} results")
            res_obj = {"results": [r.to_dict() for r in results]}
            if groups is not None or (expand is not None and expand != "none"):
                res_obj["scope"] = scope.to_dict()
            return res_obj

        elif tool_name == "manage_repository_relations":
            action = args.get("action")
            if not action:
                raise ValueError("Missing required argument: 'action'")

            if action == "create_group":
                group_name = args.get("group")
                if not group_name:
                    raise ValueError(
                        "Missing required argument: 'group' for create_group"
                    )
                raw_repos = args.get("repos", [])
                resolved_repos = (
                    self.service.resolve_repo_ids(raw_repos) if raw_repos else []
                )
                group = self.service.repo_manager.create_group(
                    group_name, resolved_repos
                )
                return {"group": group.to_dict()}

            elif action == "delete_group":
                group_name = args.get("group")
                if not group_name:
                    raise ValueError(
                        "Missing required argument: 'group' for delete_group"
                    )
                ok = self.service.repo_manager.delete_group(group_name)
                return {"success": ok, "deleted_group": group_name}

            elif action == "add_to_group":
                group_name = args.get("group")
                if not group_name:
                    raise ValueError(
                        "Missing required argument: 'group' for add_to_group"
                    )
                raw_repos = args.get("repos", [])
                if not raw_repos:
                    raise ValueError(
                        "Missing required argument: 'repos' for add_to_group"
                    )
                resolved_repos = self.service.resolve_repo_ids(raw_repos) or []
                group = self.service.repo_manager.add_repos_to_group(
                    group_name, resolved_repos
                )
                return {"group": group.to_dict()}

            elif action == "remove_from_group":
                group_name = args.get("group")
                if not group_name:
                    raise ValueError(
                        "Missing required argument: 'group' for remove_from_group"
                    )
                raw_repos = args.get("repos", [])
                if not raw_repos:
                    raise ValueError(
                        "Missing required argument: 'repos' for remove_from_group"
                    )
                resolved_repos = self.service.resolve_repo_ids(raw_repos) or []
                removed_count = 0
                for r in resolved_repos:
                    if self.service.repo_manager.remove_repo_from_group(group_name, r):
                        removed_count += 1
                return {"success": True, "removed_count": removed_count}

            elif action == "add_dependency":
                raw_repo = args.get("repo")
                raw_dep = args.get("depends_on")
                if not raw_repo or not raw_dep:
                    raise ValueError(
                        "Missing required arguments: 'repo' and 'depends_on' for add_dependency"
                    )
                resolved_repo = (
                    self.service.resolve_repo_ids([raw_repo]) or [raw_repo]
                )[0]
                resolved_dep = (self.service.resolve_repo_ids([raw_dep]) or [raw_dep])[
                    0
                ]
                dep = self.service.repo_manager.add_dependency(
                    resolved_repo, resolved_dep
                )
                return {"dependency": dep.to_dict()}

            elif action == "remove_dependency":
                raw_repo = args.get("repo")
                raw_dep = args.get("depends_on")
                if not raw_repo or not raw_dep:
                    raise ValueError(
                        "Missing required arguments: 'repo' and 'depends_on' for remove_dependency"
                    )
                resolved_repo = (
                    self.service.resolve_repo_ids([raw_repo]) or [raw_repo]
                )[0]
                resolved_dep = (self.service.resolve_repo_ids([raw_dep]) or [raw_dep])[
                    0
                ]
                ok = self.service.repo_manager.remove_dependency(
                    resolved_repo, resolved_dep
                )
                return {
                    "success": ok,
                    "removed_dependency": f"{resolved_repo}->{resolved_dep}",
                }

            else:
                raise ValueError(
                    f"Unknown action for manage_repository_relations: '{action}'"
                )

        elif tool_name == "get_repository_relations":
            raw_repo = args.get("repo")
            if raw_repo:
                resolved_repo = (
                    self.service.resolve_repo_ids([raw_repo]) or [raw_repo]
                )[0]
                return self.service.repo_manager.get_repo_relations(resolved_repo)
            else:
                groups = self.service.repo_manager.list_groups()
                repos = self.service.list_repositories()
                all_deps = []
                for r in repos:
                    for d in self.service.repo_manager.get_dependencies(r.repo_id):
                        all_deps.append({"repo_id": r.repo_id, "depends_on": d})
                return {
                    "groups": [g.to_dict() for g in groups],
                    "dependencies": all_deps,
                }

        elif tool_name == "get_symbol_definition":
            sym = args.get("symbol_name", "")
            repo = args.get("repo_id")
            return self.service.get_symbol_info(sym, repo)

        elif tool_name == "get_call_hierarchy":
            sym = args.get("symbol_name", "")
            repo = args.get("repo_id")
            return self.service.get_symbol_info(sym, repo)

        elif tool_name == "get_cross_repo_api_links":
            return {"links": self.service.get_cross_repo_api_links()}

        elif tool_name == "list_repositories":
            repos = self.service.list_repositories()
            return {"repositories": [r.to_dict() for r in repos]}

        elif tool_name == "update_repository":
            repo_id = args.get("repo_id")
            branch = args.get("branch")
            url = args.get("url")
            name = args.get("name")
            auto_sync = args.get("auto_sync", True)
            return self.service.update_repository(
                repo_id=repo_id,
                url_or_path=url,
                branch=branch,
                name=name,
                auto_sync=auto_sync,
            )

        elif tool_name == "sync_repository":
            repo_id = args.get("repo_id")
            force = args.get("force", False)
            if not repo_id:
                raise ValueError("Missing required argument: 'repo_id'")
            return self.service.sync_repository(repo_id=repo_id, force=force)

        else:
            logger.warning(f"Unknown MCP tool requested: '{tool_name}'")
            raise ValueError(f"Unknown tool: {tool_name}")

    def run_stdio(self):
        """Runs the MCP server reading from standard input."""
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
                resp = self.handle_request(req)
                if resp:
                    sys.stdout.write(json.dumps(resp) + "\n")
                    sys.stdout.flush()
            except Exception as e:
                err_resp = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": f"Parse error: {str(e)}"},
                }
                sys.stdout.write(json.dumps(err_resp) + "\n")
                sys.stdout.flush()


def run_mcp_server(data_dir: Optional[str] = None):
    canonical_dir = resolve_data_dir(data_dir)
    server = MCPServer(data_dir=canonical_dir)
    try:
        server.run_stdio()
    finally:
        server.service.shutdown()
