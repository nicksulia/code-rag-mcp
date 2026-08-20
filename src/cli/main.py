"""
Command-line interface (CLI) for Multi-Repository Code RAG with Advanced Indexing.
"""

import sys
import os
import logging
import argparse
from pathlib import Path

from ..service import MultiRepoRAGService
from ..indexer.embeddings import DEFAULT_OLLAMA_EMBEDDING_MODEL
from ..instance_lock import InstanceLockError
from ..server.api import start_server
from ..mcp.server import run_mcp_server


def print_banner():
    print("=" * 65)
    print(" 🚀 Multi-Repository Code RAG Engine (OpenSpec Powered)")
    print("=" * 65)


def check_and_ensure_embedding_engine(
    service: MultiRepoRAGService, auto_pull: bool = False
):
    engine = service.embedding_engine
    if hasattr(engine, "ensure_model_ready"):
        status = engine.ensure_model_ready()
        if status.get("ok"):
            dim = status.get("dimension")
            dim_text = f" (dim={dim})" if dim else " (dimension resolved on first use)"
            print(f"✅ Ollama Embedding Model Ready: '{engine.model}'{dim_text}")
        else:
            if not engine.is_server_online():
                print(
                    f"\n⚠️  Ollama Connection Notice: Ollama server is offline at {engine.host}"
                )
                print(
                    f"   Using built-in syntax-weighted offline encoder until Ollama is started (`ollama serve`).\n"
                )
            elif status.get("can_pull"):
                print(
                    f"\n⚠️  Ollama Model Notice: '{engine.model}' is not installed in local Ollama."
                )
                should_pull = auto_pull
                if not should_pull and sys.stdin.isatty():
                    try:
                        ans = (
                            input(
                                f"   Would you like to pull '{engine.model}' now? [Y/n]: "
                            )
                            .strip()
                            .lower()
                        )
                        should_pull = ans in ("", "y", "yes")
                    except (KeyboardInterrupt, EOFError):
                        should_pull = False

                if should_pull:
                    print(f"📥 Pulling '{engine.model}' from Ollama library...")

                    def pull_progress(msg, completed, total):
                        if total > 0:
                            pct = int((completed / total) * 100)
                            mb_c = completed / (1024 * 1024)
                            mb_t = total / (1024 * 1024)
                            sys.stdout.write(
                                f"\r   [{pct:3d}%] {msg}: {mb_c:.1f} MB / {mb_t:.1f} MB"
                            )
                            sys.stdout.flush()
                        else:
                            sys.stdout.write(f"\r   {msg:<60}")
                            sys.stdout.flush()

                    ok = engine.pull_model(progress_callback=pull_progress)
                    print()
                    if ok:
                        print(f"✅ Successfully downloaded '{engine.model}'!\n")
                    else:
                        print(
                            f"❌ Failed to download '{engine.model}'. Using offline fallback encoder.\n"
                        )
                else:
                    print(
                        f"   To download later, run: "
                        f"`{status.get('pull_command', f'ollama pull {engine.model}')}`\n"
                    )


def cmd_add(service: MultiRepoRAGService, args):
    print(f"Registering repository '{args.repo_id}' from '{args.path}'...")
    repo = service.add_repository(
        repo_id=args.repo_id,
        name=args.name or args.repo_id,
        source_type=args.type,
        url_or_path=args.path,
        branch=args.branch,
        auto_sync=not args.no_sync,
    )
    print(f"✅ Repository registered: {repo.name} ({repo.status.value})")
    print(
        f"   Indexed files: {repo.total_files} | Chunks: {repo.total_chunks} | Symbols: {repo.total_symbols}"
    )


def render_progress_bar(current: int, total: int, filename: str, phase: str):
    if total <= 0:
        return
    pct = int((current / total) * 100)
    bar_width = 30
    filled = int(bar_width * (current / total))
    bar = "█" * filled + "░" * (bar_width - filled)

    clean_fn = filename if len(filename) < 40 else "..." + filename[-37:]
    sys.stdout.write(f"\r  [{bar}] {pct:3d}% ({current}/{total}) | {clean_fn:<40}")
    sys.stdout.flush()
    if current >= total and phase == "index":
        sys.stdout.write("\n")


def cmd_sync(service: MultiRepoRAGService, args):
    repos = service.list_repositories()
    if not repos:
        print(
            "No repositories registered. Use `python3 main.py add <id> <path>` first."
        )
        return

    target_repo_id = (
        None
        if (getattr(args, "all", False) or args.repo_id in ("all", "*"))
        else args.repo_id
    )
    sync_all_flag = getattr(args, "all", False) or args.repo_id in ("all", "*")

    # Interactive prompt if requested or if no repo_id was provided (unless --all was specified)
    if not sync_all_flag and (
        getattr(args, "interactive", False)
        or (not target_repo_id and sys.stdin.isatty())
    ):
        print("\n" + "=" * 65)
        print(" 🔄 Interactive Multi-Repository Synchronizer")
        print("=" * 65)
        print(f" Embedding Engine : {service.embedding_engine.__class__.__name__}")
        if hasattr(service.embedding_engine, "model"):
            print(f" Model Target     : {service.embedding_engine.model}")
        print(f" Target Dimension : {service.embedding_engine.dimension} dims")
        print("-" * 65)
        print("Available Repositories:")
        for idx, r in enumerate(repos, start=1):
            print(
                f"  [{idx}] {r.repo_id:<30} ({r.total_files} files, {r.total_chunks} chunks)"
            )
        print(f"  [{len(repos) + 1}] 🚀 Sync All Repositories")
        print("=" * 65)

        try:
            choice = input(
                f"Select repository to sync [1-{len(repos) + 1}] (default: {len(repos) + 1}): "
            ).strip()
            if choice == "" or choice == str(len(repos) + 1):
                target_repo_id = None
            else:
                choice_idx = int(choice) - 1
                if 0 <= choice_idx < len(repos):
                    target_repo_id = repos[choice_idx].repo_id
                else:
                    target_repo_id = None
        except (KeyboardInterrupt, EOFError):
            print("\nAborted.")
            return

    if target_repo_id:
        resolved = service.resolve_repo_ids([target_repo_id])
        matched_repo = (
            service.repo_manager.get_repo(resolved[0])
            if resolved
            else service.repo_manager.get_repo(target_repo_id)
        )
        if not matched_repo:
            print(
                f"❌ Repository '{target_repo_id}' not found. Run `python3 main.py list` to see available repositories."
            )
            return
        target_list = [matched_repo]
    else:
        target_list = repos

    for r in target_list:
        if not r:
            continue
        print(f"\n⚡ Synchronizing: {r.repo_id} ({r.url_or_path})")
        print(
            f"   Model: {getattr(service.embedding_engine, 'model', 'subword')} (dim={service.embedding_engine.dimension})"
        )

        try:
            res = service.sync_repository(
                repo_id=r.repo_id,
                force=getattr(args, "force", False),
                progress_callback=render_progress_bar,
            )
            print(f"\n┌────────────────────────────────────────────────────────────┐")
            print(f"│ ✅ Sync Complete: {r.repo_id:<41}│")
            print(f"├────────────────────────────────────────────────────────────┤")
            print(
                f"│ 📦 Files Indexed   : {res['added_files'] + res['modified_files']} updated / {r.total_files} total files{' ':<16}│"
            )
            print(
                f"│ 🧩 Code Chunks     : {res['total_chunks']} chunks (dim={res['embedding_dimension']}){' ':<24}│"
            )
            print(f"│ 🕸️ Extracted Symbols: {res['total_symbols']} symbols{' ':<31}│")
            print(f"│ ⏱️ Elapsed Time    : {res['elapsed_seconds']}s{' ':<38}│")
            print(f"└────────────────────────────────────────────────────────────┘")
        except Exception as e:
            print(f"\n❌ Sync failed for {r.repo_id}: {str(e)}")


def cmd_list(service: MultiRepoRAGService, args):
    repos = service.list_repositories()
    if not repos:
        print("No repositories registered.")
        return
    print(f"\nIndexed Repositories ({len(repos)}):")
    print("-" * 75)
    print(
        f"{'ID':<18} {'STATUS':<10} {'FILES':<8} {'CHUNKS':<8} {'SYMBOLS':<8} {'PATH/URL'}"
    )
    print("-" * 75)
    for r in repos:
        print(
            f"{r.repo_id:<18} {r.status.value:<10} {r.total_files:<8} {r.total_chunks:<8} {r.total_symbols:<8} {r.url_or_path}"
        )
    print("-" * 75)


from ..ingestion.repo_manager import RelationError


def ensure_index_ready(service: MultiRepoRAGService):
    """Runs the automatic re-embed when the configured model changed."""
    stale = service.get_stale_repo_ids()
    if not stale:
        return
    print(
        f"♻️  Embedding model changed - re-embedding {len(stale)} repository(ies) "
        f"with '{service.embedding_model_name}'..."
    )
    service.reindex_stale_repos(progress_callback=render_progress_bar)
    print("\n✅ Dense index rebuilt.\n")


def cmd_search(service: MultiRepoRAGService, args):
    print(f'🔍 Searching codebases for: "{args.query}" (BM25F + Multi-Vector)...\n')
    ensure_index_ready(service)
    repo_ids = [args.repo] if args.repo else None
    groups = args.group
    expand = args.expand
    expand_depth = args.expand_depth

    try:
        scope = service.resolve_scope(
            repo_ids=repo_ids, groups=groups, expand=expand, expand_depth=expand_depth
        )
        if groups or (expand and expand != "none"):
            primary_str = ", ".join(scope.primary) if scope.primary else "all"
            expanded_str = (
                ", ".join(
                    [
                        f"{k} ({v.direction}, {v.hops}h)"
                        for k, v in scope.expanded.items()
                    ]
                )
                if scope.expanded
                else "none"
            )
            print(f"🎯 Resolved Scope:")
            print(f"   Primary Repos  : {primary_str}")
            print(f"   Expanded Repos : {expanded_str}\n")

        results = service.search(
            args.query,
            repo_ids=repo_ids,
            groups=groups,
            expand=expand,
            expand_depth=expand_depth,
            top_k=args.top_k,
        )
    except RelationError as e:
        print(f"❌ Relation error: {e}")
        sys.exit(1)

    if not results:
        print("No results found.")
        return

    for idx, r in enumerate(results, start=1):
        c = r.chunk
        sym_str = f" > {c.symbol_name}" if c.symbol_name else ""
        exp_badge = (
            f" [⚡ EXPANDED: {r.relation_direction}, {r.relation_hops} hop]"
            if r.repo_relation == "expanded"
            else ""
        )
        print(
            f"[{idx}] Score: {r.score:.4f} | {c.repo_id}{exp_badge} :: {c.file_path}:L{c.start_line}-L{c.end_line}{sym_str}"
        )
        if r.related_callers:
            callers_str = ", ".join(
                f"{x['source_repo']}:{x['source_symbol']}" for x in r.related_callers
            )
            print(f"    ↳ Called by: {callers_str}")
        print(f"    {c.raw_content.splitlines()[0][:90]}")
        print()


def cmd_group(service: MultiRepoRAGService, args):
    action = args.group_action
    if not action or action == "list":
        groups = service.repo_manager.list_groups()
        if not groups:
            print("No repository groups configured.")
            return
        print(f"\nRepository Groups ({len(groups)}):")
        print("-" * 65)
        for g in groups:
            members_str = ", ".join(g.members) if g.members else "(no members)"
            print(f"🏷️  {g.name:<20} : {members_str}")
        print("-" * 65)
        return

    try:
        if action == "create":
            repos = service.resolve_repo_ids(args.repos or []) or []
            g = service.repo_manager.create_group(args.name, repos)
            print(f"✅ Created group '{g.name}' with members: {g.members}")

        elif action == "delete":
            ok = service.repo_manager.delete_group(args.name)
            if ok:
                print(f"✅ Deleted group '{args.name}'")
            else:
                print(f"❌ Group '{args.name}' not found")
                sys.exit(1)

        elif action == "add":
            repos = service.resolve_repo_ids(args.repos) or []
            g = service.repo_manager.add_repos_to_group(args.name, repos)
            print(f"✅ Added {repos} to group '{g.name}'. Current members: {g.members}")

        elif action == "remove":
            repos = service.resolve_repo_ids(args.repos) or []
            for r in repos:
                service.repo_manager.remove_repo_from_group(args.name, r)
            g = service.repo_manager.get_group(args.name)
            print(
                f"✅ Removed {repos} from group '{args.name}'. Current members: {g.members if g else []}"
            )

        elif action == "show":
            members = service.repo_manager.get_group_members(args.name)
            print(
                f"Group '{args.name}' members ({len(members)}): {', '.join(members) if members else 'None'}"
            )
    except RelationError as e:
        print(f"❌ Relation error: {e}")
        sys.exit(1)


def cmd_relation(service: MultiRepoRAGService, args):
    action = args.relation_action
    if not action or action == "list":
        repos = service.list_repositories()
        print("\nRepository Dependency Relations:")
        print("-" * 65)
        found = False
        for r in repos:
            deps = service.repo_manager.get_dependencies(r.repo_id)
            if deps:
                found = True
                print(f"🔗 {r.repo_id} -> depends on -> {', '.join(deps)}")
        if not found:
            print("No dependency relations declared.")
        print("-" * 65)
        return

    try:
        if action == "add":
            repo = (service.resolve_repo_ids([args.repo]) or [args.repo])[0]
            dep = (service.resolve_repo_ids([args.depends_on]) or [args.depends_on])[0]
            service.repo_manager.add_dependency(repo, dep)
            print(f"✅ Added dependency: {repo} -> depends on -> {dep}")

        elif action == "remove":
            repo = (service.resolve_repo_ids([args.repo]) or [args.repo])[0]
            dep = (service.resolve_repo_ids([args.depends_on]) or [args.depends_on])[0]
            ok = service.repo_manager.remove_dependency(repo, dep)
            if ok:
                print(f"✅ Removed dependency: {repo} -> {dep}")
            else:
                print(f"❌ Dependency edge {repo} -> {dep} not found")
                sys.exit(1)

        elif action == "show":
            repo = (service.resolve_repo_ids([args.repo]) or [args.repo])[0]
            rel = service.repo_manager.get_repo_relations(repo)
            print(f"\nRelations for repository '{repo}':")
            print(
                f"  🏷️ Groups       : {', '.join(rel['groups']) if rel['groups'] else 'None'}"
            )
            print(
                f"  🔗 Depends on   : {', '.join(rel['dependencies']) if rel['dependencies'] else 'None'}"
            )
            print(
                f"  👥 Dependents   : {', '.join(rel['dependents']) if rel['dependents'] else 'None'}\n"
            )
    except RelationError as e:
        print(f"❌ Relation error: {e}")
        sys.exit(1)


def cmd_serve(service: MultiRepoRAGService, args):
    check_and_ensure_embedding_engine(
        service, auto_pull=getattr(args, "auto_pull", False)
    )
    print(f"\n🚀 Starting Multi-Repository Code RAG Server...")
    print(f"   Web Dashboard: http://{args.host}:{args.port}")
    print(f"   REST API:      http://{args.host}:{args.port}/api/v1")
    state = service.embedding_runtime_state()
    print(
        f"   Embedding:     {getattr(service.embedding_engine, 'model', 'subword')} "
        f"(loaded on demand, policy: {state['policy']['mode']})"
    )
    stale = service.get_stale_repo_ids()
    if stale:
        print(
            f"   ♻️  Embedding model changed: {len(stale)} repository(ies) will be "
            f"re-embedded on the first search."
        )
    print(f"   Press Ctrl+C to stop.\n")

    start_server(service, host=args.host, port=args.port)


def cmd_update(service: MultiRepoRAGService, args):
    print(f"🔄 Updating repository '{args.repo_id}'...")
    try:
        res = service.update_repository(
            repo_id=args.repo_id,
            url_or_path=args.url,
            branch=args.branch,
            name=args.name,
            auto_sync=not args.no_sync,
        )
        repo_data = res["repo"]
        print(f"✅ Repository '{args.repo_id}' updated successfully!")
        print(f"   Name        : {repo_data['name']}")
        print(f"   Branch      : {repo_data['branch']}")
        print(f"   URL / Path  : {repo_data['url_or_path']}")
        print(
            f"   Total Files : {repo_data['total_files']} | Chunks: {repo_data['total_chunks']} | Symbols: {repo_data['total_symbols']}"
        )
        if res.get("sync_result"):
            sync_r = res["sync_result"]
            print(
                f"   Sync Status : {sync_r['status']} ({sync_r.get('added_files', 0)} added, {sync_r.get('modified_files', 0)} modified)"
            )
    except Exception as e:
        print(f"❌ Failed to update repository '{args.repo_id}': {e}")


def cmd_unload(service: MultiRepoRAGService, args):
    print("🧹 Unloading models from Ollama / GPU VRAM...")
    res = service.unload_models()
    if res.get("busy"):
        print(
            f"⏳ Embedding model is busy ({res.get('active_operations', 0)} operation(s) in flight); "
            f"unload skipped."
        )
        return
    print("✅ Model unload complete:", res)


def cmd_mcp(service: MultiRepoRAGService, args):
    run_mcp_server(data_dir=service.data_dir)


def main():
    parser = argparse.ArgumentParser(
        description="Multi-Repository Code RAG CLI with Advanced Indexing"
    )
    parser.add_argument(
        "--data-dir", default="./data", help="Directory for index databases"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose debug logging"
    )
    parser.add_argument("--debug", action="store_true", help="Alias for --verbose")
    parser.add_argument(
        "--embedding-provider",
        choices=["ollama", "subword", "unsloth", "vllm", "gemini", "openai"],
        default="ollama",
        help="Pluggable embedding provider (default: ollama)",
    )
    parser.add_argument(
        "--embedding-model",
        default=None,
        help=f"Embedding model name (default: {DEFAULT_OLLAMA_EMBEDDING_MODEL}, "
        f"or $OLLAMA_EMBEDDING_MODEL)",
    )
    parser.add_argument(
        "--keep-alive",
        default=None,
        help="Embedding model residency: '0' (release immediately), a duration "
        "like '30s'/'5m' (idle grace, default 30s), or 'always' to keep it resident. "
        "Overrides $EMBEDDING_KEEP_ALIVE.",
    )
    parser.add_argument(
        "--allow-multi-instance",
        action="store_true",
        help="Warn instead of failing when another instance already owns the data directory",
    )
    parser.add_argument(
        "--auto-pull",
        action="store_true",
        help="Automatically pull missing Ollama embedding models without interactive prompt",
    )
    parser.add_argument(
        "--ollama-host",
        default=None,
        help="Ollama server host (default: http://localhost:11434)",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # add
    p_add = subparsers.add_parser("add", help="Register a repository")
    p_add.add_argument("repo_id", help="Unique repository ID")
    p_add.add_argument("path", help="Local directory path or git URL")
    p_add.add_argument("--name", help="Display name")
    p_add.add_argument("--type", choices=["local", "git"], default="local")
    p_add.add_argument("--branch", default="main")
    p_add.add_argument("--no-sync", action="store_true", help="Do not sync immediately")

    # update
    p_update = subparsers.add_parser(
        "update", help="Update repository branch, URL/path, or display name"
    )
    p_update.add_argument("repo_id", help="Repository ID to update")
    p_update.add_argument("--branch", "-b", help="New Git branch to switch to")
    p_update.add_argument("--url", "-u", help="New Git remote URL or local path")
    p_update.add_argument("--name", "-n", help="New display name")
    p_update.add_argument(
        "--no-sync",
        action="store_true",
        help="Skip automatic synchronization after update",
    )

    # sync
    p_sync = subparsers.add_parser("sync", help="Synchronize repository")
    p_sync.add_argument(
        "repo_id",
        nargs="?",
        help="Repo ID to sync (or 'all' / omit for interactive/all)",
    )
    p_sync.add_argument(
        "-a",
        "--all",
        action="store_true",
        help="Synchronize all registered repositories",
    )
    p_sync.add_argument(
        "-i",
        "--interactive",
        action="store_true",
        help="Launch interactive repository selector menu",
    )
    p_sync.add_argument(
        "-f", "--force", action="store_true", help="Force full re-indexing of all files"
    )
    p_sync.add_argument(
        "--auto-pull",
        action="store_true",
        help="Automatically pull Ollama model if missing",
    )

    # list
    subparsers.add_parser("list", help="List registered repositories")

    # group
    p_group = subparsers.add_parser("group", help="Manage repository groups")
    p_group_sub = p_group.add_subparsers(dest="group_action", help="Group actions")
    p_group_sub.add_parser("list", help="List all repository groups")
    p_g_create = p_group_sub.add_parser("create", help="Create a repository group")
    p_g_create.add_argument("name", help="Group name")
    p_g_create.add_argument("--repos", nargs="*", help="Initial repository members")
    p_g_del = p_group_sub.add_parser("delete", help="Delete a repository group")
    p_g_del.add_argument("name", help="Group name to delete")
    p_g_add = p_group_sub.add_parser("add", help="Add repositories to a group")
    p_g_add.add_argument("name", help="Group name")
    p_g_add.add_argument("repos", nargs="+", help="Repository IDs to add")
    p_g_rem = p_group_sub.add_parser("remove", help="Remove repositories from a group")
    p_g_rem.add_argument("name", help="Group name")
    p_g_rem.add_argument("repos", nargs="+", help="Repository IDs to remove")
    p_g_show = p_group_sub.add_parser("show", help="Show members of a group")
    p_g_show.add_argument("name", help="Group name")

    # relation
    p_rel = subparsers.add_parser(
        "relation", help="Manage repository dependencies and relations"
    )
    p_rel_sub = p_rel.add_subparsers(dest="relation_action", help="Relation actions")
    p_rel_sub.add_parser("list", help="List all declared repository dependencies")
    p_r_add = p_rel_sub.add_parser(
        "add", help="Declare a dependency between repositories"
    )
    p_r_add.add_argument("repo", help="Dependent repository (source)")
    p_r_add.add_argument("depends_on", help="Dependency target repository")
    p_r_rem = p_rel_sub.add_parser(
        "remove", help="Remove a dependency between repositories"
    )
    p_r_rem.add_argument("repo", help="Dependent repository (source)")
    p_r_rem.add_argument("depends_on", help="Dependency target repository")
    p_r_show = p_rel_sub.add_parser("show", help="Show all relations for a repository")
    p_r_show.add_argument("repo", help="Repository ID")

    # search
    p_search = subparsers.add_parser("search", help="Search across indexed codebases")
    p_search.add_argument("query", help="Search query")
    p_search.add_argument("--repo", help="Filter by repo ID")
    p_search.add_argument(
        "--group", action="append", help="Filter by group name (can specify multiple)"
    )
    p_search.add_argument(
        "--expand",
        choices=["none", "upstream", "downstream", "both"],
        default="none",
        help="Expand scope along repository dependency graph (default: none)",
    )
    p_search.add_argument(
        "--expand-depth",
        type=int,
        default=1,
        help="Max hops for dependency expansion (default: 1)",
    )
    p_search.add_argument("--top-k", type=int, default=8, help="Number of results")

    # serve
    p_serve = subparsers.add_parser(
        "serve", help="Start the Web UI and REST API server"
    )
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.add_argument(
        "--auto-pull",
        action="store_true",
        help="Automatically pull Ollama model if missing",
    )

    # unload
    subparsers.add_parser(
        "unload", help="Unload models from Ollama / GPU VRAM immediately"
    )

    # mcp
    subparsers.add_parser("mcp", help="Run MCP server over stdio for AI IDEs")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(0)

    # Configure logging
    log_level = (
        logging.DEBUG
        if (
            args.verbose
            or args.debug
            or os.environ.get("VERBOSE")
            or os.environ.get("LOG_LEVEL") == "DEBUG"
        )
        else logging.INFO
    )
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        service = MultiRepoRAGService(
            data_dir=args.data_dir,
            embedding_provider=args.embedding_provider,
            embedding_model=args.embedding_model,
            ollama_host=args.ollama_host,
            keep_alive=args.keep_alive,
            allow_multi_instance=args.allow_multi_instance,
        )
    except InstanceLockError as e:
        print(f"❌ {e}")
        sys.exit(1)

    try:
        if args.command == "add":
            cmd_add(service, args)
        elif args.command == "update":
            cmd_update(service, args)
        elif args.command == "sync":
            cmd_sync(service, args)
        elif args.command == "list":
            cmd_list(service, args)
        elif args.command == "group":
            cmd_group(service, args)
        elif args.command == "relation":
            cmd_relation(service, args)
        elif args.command == "search":
            cmd_search(service, args)
        elif args.command == "serve":
            cmd_serve(service, args)
        elif args.command == "unload":
            cmd_unload(service, args)
        elif args.command == "mcp":
            cmd_mcp(service, args)
    finally:
        # Release the embedding model and the instance lock on exit.
        service.shutdown()


if __name__ == "__main__":
    main()
