import argparse
import json
import shutil
import sys
from pathlib import Path

from pydantic import ValidationError

from rag_workbench.application.evaluation import EvalCase, evaluate
from rag_workbench.bootstrap import build_services, load_settings
from rag_workbench.config import Settings, load_file_settings
from rag_workbench.domain.errors import AppError
from rag_workbench.infrastructure.repository import Repository


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RAG Workbench local tools")
    parser.add_argument("--data-dir", type=Path, help="Override isolated workspace data directory")
    parser.add_argument("--settings-file", type=Path, help="Explicit non-secret JSON settings")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("stats", help="Show current KB health without loading a model")
    sub.add_parser("reset-settings", help="Reset saved UI overrides; preserves documents and chats")
    prune = sub.add_parser(
        "prune-indexes", help="Preview obsolete generated indexes; deletion is opt-in"
    )
    prune.add_argument("--keep", type=int, default=2)
    prune.add_argument("--apply", action="store_true")
    sub.add_parser("sessions", help="List conversations")
    rename = sub.add_parser("rename-session")
    rename.add_argument("session_id")
    rename.add_argument("title")
    remove_session = sub.add_parser("delete-session")
    remove_session.add_argument("session_id")
    ingest = sub.add_parser("ingest", help="Import files and rebuild with real embedding model")
    ingest.add_argument("files", type=Path, nargs="+")
    sub.add_parser("rebuild", help="Rebuild the current index from original files")
    delete = sub.add_parser("delete", help="Remove a document and archive its original")
    delete.add_argument("document_id")
    restore = sub.add_parser("restore", help="Reimport an archived original")
    restore.add_argument("document_id")
    query = sub.add_parser("search", help="Real retrieval, no paid API")
    query.add_argument("question")
    ask = sub.add_parser("ask", help="Answer using configured LLM (remote API)")
    ask.add_argument("question")
    sample = sub.add_parser("sample", help="Import examples into data/sandbox only")
    sample.add_argument("--clear", action="store_true", help="Clear only the marked sandbox")
    evaluation = sub.add_parser("evaluate", help="Evaluate a user-labelled JSONL dataset")
    evaluation.add_argument("dataset", type=Path)
    evaluation.add_argument(
        "--with-routing", action="store_true", help="Calls remote, potentially paid API"
    )
    return parser


def sample_workspace(clear: bool) -> dict:
    # Use CWD as documented, and never accept an arbitrary deletion path from user input.
    project = Path.cwd().resolve()
    sandbox = project / "data" / "sandbox"
    marker = sandbox / ".rag-sandbox"
    if (
        sandbox.is_symlink()
        or sandbox.is_junction()
        or (
            sandbox.parent.exists()
            and (sandbox.parent.is_symlink() or sandbox.parent.is_junction())
        )
    ):
        raise AppError("unsafe_sandbox", "样例路径不能是链接或联接目录。")
    if sandbox.exists() and not marker.is_file():
        raise AppError("unsafe_sandbox", "现有目录没有隔离样例标识，不会将其标记为可清空目录。")
    if clear:
        if sandbox.exists():
            if sandbox.is_symlink() or sandbox.is_junction() or not marker.is_file():
                raise AppError("unsafe_sandbox", "缺少隔离样例标识或路径是链接，拒绝清理。")
            resolved = sandbox.resolve()
            if resolved != project / "data" / "sandbox" or project not in resolved.parents:
                raise AppError("unsafe_sandbox", "样例目录不在项目内部，拒绝清理。")
            shutil.rmtree(resolved)
        return {"sandbox_cleared": True, "production_unchanged": True}
    if not (project / "examples").is_dir():
        raise AppError("missing_examples", "请从项目根目录运行；examples 目录不存在。")
    sandbox.mkdir(parents=True, exist_ok=True)
    marker.write_text("RAG Workbench disposable examples\n", encoding="utf-8")
    services = build_services(Settings(_env_file=None, data_dir=sandbox))
    for path in sorted((project / "examples").glob("*")):
        if path.suffix.lower() in {".txt", ".md", ".pdf"} and path.name != "README.md":
            services.knowledge.ingest(path.name, path.read_bytes())
    return services.knowledge.stats()


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = make_parser().parse_args()

    def deployment_settings() -> Settings:
        if args.settings_file:
            return load_file_settings(args.settings_file, data_dir=args.data_dir)
        return Settings(data_dir=args.data_dir) if args.data_dir else Settings()

    try:
        if args.command == "sample":
            output = sample_workspace(args.clear)
        elif args.command == "reset-settings":
            settings = deployment_settings()
            Repository(settings.data_dir.resolve() / "metadata.sqlite3").set_state(
                "ui_settings", {}
            )
            output = {"ui_settings_reset": True, "documents_and_chats_preserved": True}
        else:
            settings = deployment_settings()
            services = build_services(load_settings(settings))
            kb = services.knowledge
            if args.command == "stats":
                output = kb.stats()
            elif args.command == "prune-indexes":
                output = {
                    "applied": args.apply,
                    "obsolete_generations": kb.prune_indexes(args.keep, apply=args.apply),
                }
            elif args.command == "sessions":
                output = services.repo.sessions()
            elif args.command == "rename-session":
                services.repo.rename_session(args.session_id, args.title)
                output = {"renamed": args.session_id}
            elif args.command == "delete-session":
                services.repo.delete_session(args.session_id)
                output = {"deleted": args.session_id}
            elif args.command == "ingest":
                output = [
                    d.model_dump()
                    for d in kb.ingest_many([(p.name, p.read_bytes()) for p in args.files])
                ]
                if any(d["status"] == "failed" for d in output):
                    print(json.dumps(output, ensure_ascii=False, indent=2))
                    return 1
            elif args.command == "rebuild":
                kb.rebuild()
                output = kb.stats()
            elif args.command == "delete":
                kb.delete(args.document_id)
                output = kb.stats()
            elif args.command == "restore":
                output = kb.restore(args.document_id).model_dump()
            elif args.command == "search":
                output = [r.model_dump() for r in kb.retrieve(args.question)]
            elif args.command == "ask":
                result = services.pipeline.answer(args.question)
                output = result.model_dump()
                if result.mode == "error":
                    print(json.dumps(output, ensure_ascii=False, indent=2))
                    return 1
            else:
                cases = [
                    EvalCase.model_validate_json(line)
                    for line in args.dataset.read_text(encoding="utf-8-sig").splitlines()
                    if line.strip()
                ]
                output = evaluate(services.pipeline, cases, with_routing=args.with_routing)
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    except AppError as exc:
        print(f"{exc.code}: {exc}", file=sys.stderr)
        return 1
    except (ValueError, ValidationError, OSError) as exc:
        print(f"操作失败（{type(exc).__name__}）。请检查输入文件、配置与权限。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
