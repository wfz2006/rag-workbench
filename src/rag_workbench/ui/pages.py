from datetime import datetime
from pathlib import Path

import streamlit as st
from pydantic import ValidationError

from rag_workbench.application.packs import (
    PackBuilder,
    PackCatalog,
    PackSpec,
    load_pack_settings,
    pack_lint,
)
from rag_workbench.bootstrap import (
    Services,
    environment_owned_keys,
    reset_ui_settings,
    save_ui_settings,
)
from rag_workbench.domain.errors import AppError
from rag_workbench.infrastructure.embedding import BgeEmbedder
from rag_workbench.infrastructure.parsing import SUPPORTED_FORMATS_LABEL, UPLOAD_EXTENSIONS
from rag_workbench.ui.components import inspection_panel
from rag_workbench.ui.pack_studio import render_pack_studio
from rag_workbench.ui.shell import activate_pack

INDEX_LABELS = {
    "ready": "已就绪",
    "empty": "空知识库",
    "incompatible": "需要重建",
    "corrupt": "索引损坏",
}
DOCUMENT_LABELS = {"ready": "已就绪", "pending": "待处理", "failed": "导入失败"}
PACK_STATUS_LABELS = {"draft": "草稿", "reviewed": "已审核", "frozen": "已冻结"}
PROFILE_STATUS_LABELS = {"draft": "profile 草稿", "reviewed": "profile 已审核"}
EVAL_STATUS_LABELS = {"none": "无评测集", "candidate": "评测题候选中", "frozen": "评测集已冻结"}
LICENSE_LABELS = {
    "unknown": "未知（默认仅本机）",
    "internal_only": "内部资料（仅本机）",
    "licensed": "已获授权",
    "public_domain": "公版/公开",
}
LINT_STATUS_LABELS = {"pass": "通过", "warn": "警告", "fail": "失败"}


def document_rows(documents):
    return [
        {
            "文档名称": d.name,
            "状态": DOCUMENT_LABELS[d.status],
            "上传时间": datetime.fromisoformat(d.created_at).astimezone().strftime("%m-%d %H:%M"),
        }
        for d in documents
    ]


def ordered_documents(documents):
    """Newest uploads first; equal timestamps use names instead of content hashes."""
    by_name = sorted(documents, key=lambda document: (document.name.casefold(), document.id))
    return sorted(by_name, key=lambda document: document.created_at, reverse=True)


def _save_feedback(
    message: str,
    *,
    requires_reindex: bool = False,
    warning: bool = False,
    error: bool = False,
    index_status: str | None = None,
) -> None:
    if index_status is not None:
        requires_reindex = index_status not in {"ready", "empty"}
        warning = warning or requires_reindex
    st.session_state["settings_feedback"] = {
        "message": message,
        "requires_reindex": requires_reindex,
        "warning": warning,
        "error": error,
        "index_status": index_status,
    }
    st.toast(message)
    st.session_state["focus_settings_feedback"] = True


def saved_feedback(stats: dict, prefix: str = "设置已保存。") -> dict:
    requires = stats["index_status"] not in {"ready", "empty"}
    return {
        "index_status": stats["index_status"],
        "message": prefix
        + (
            "当前索引需要重建。"
            if requires
            else "当前为空知识库，后续导入使用新参数。"
            if stats["index_status"] == "empty"
            else "当前索引已就绪，无需重建索引。"
        ),
        "requires_reindex": requires,
        "warning": requires,
    }


def current_feedback(feedback: dict, index_status: str) -> dict:
    requires = index_status not in {"ready", "empty"}
    if not feedback:
        return saved_feedback({"index_status": index_status}, prefix="") if requires else {}
    if feedback.get("error"):
        # Keep a failed save visible; an index warning must not erase that error.
        return {**feedback, "requires_reindex": requires}
    if feedback.get("index_status") == index_status:
        return feedback
    if "index_status" not in feedback and requires and feedback.get("requires_reindex"):
        return feedback
    return {**saved_feedback({"index_status": index_status}, prefix=""), "error": False}


def knowledge_page(services: Services) -> None:
    st.title("知识库管理")
    st.caption("内容由你决定。更换主题无需修改业务代码。")
    stats = services.knowledge.stats()
    cols = st.columns(3)
    for col, label, value in zip(
        cols,
        ("文档", "文本块", "索引"),
        (stats["documents"], stats["chunks"], INDEX_LABELS[stats["index_status"]]),
        strict=True,
    ):
        col.metric(label, value)
    st.caption(f"Embedding：{stats['embedding_model']}")
    if stats["error"]:
        st.error(stats["error"])
    with st.container(border=True):
        st.subheader("添加文档")
        st.caption(
            f"{SUPPORTED_FORMATS_LABEL} · 每个文件至多 "
            f"{services.settings.max_upload_mb} MB · 不支持 OCR"
        )
        st.caption("隐私提示：用于证据判断与回答的片段可能发送至远程 LLM；原始文档保存在本机。")
        uploaded = st.file_uploader(
            "选择资料",
            type=UPLOAD_EXTENSIONS,
            accept_multiple_files=True,
            key=f"upload_{st.session_state.get('upload_version', 0)}",
        )
        if st.button("导入并建立索引", type="primary", disabled=not uploaded):
            successes = 0
            errors: list[str] = []
            try:
                with st.spinner("正在批量解析并建立索引；首次使用可能需要下载 Embedding 模型…"):
                    imported = services.knowledge.ingest_many(
                        [(u.name, u.getvalue()) for u in uploaded]
                    )
                successes = sum(d.status == "ready" for d in imported)
                errors.extend(f"{d.name}：{d.error}" for d in imported if d.status == "failed")
            except AppError as exc:
                errors.append(str(exc))
            if successes:
                st.session_state["flash"] = f"{successes} 个文件已处理（重复内容不会重复入库）。"
            st.session_state["upload_errors"] = errors
            st.session_state["upload_version"] = st.session_state.get("upload_version", 0) + 1
            st.rerun()
    documents = ordered_documents(services.knowledge.list_documents())
    st.subheader("文档目录")
    if not documents:
        st.info("这里还没有资料。使用上方上传区建立你的第一个知识库。")
    else:
        st.caption("按上传时间从新到旧排列；同一时间按文件名排列。")
        st.dataframe(
            document_rows(documents),
            hide_index=True,
            width="stretch",
            column_config={
                "文档名称": st.column_config.TextColumn(width=180),
                "状态": st.column_config.TextColumn(width=60),
                "上传时间": st.column_config.TextColumn(width=90, help="本地时间（月-日 时:分）"),
            },
        )
        st.caption("完整 ID、大小与错误见下方所选文档详情。")
        options = {d.id: d for d in documents}
        selected = st.selectbox(
            "选择要移除的文档",
            list(options),
            format_func=lambda key: f"{options[key].name} · {key[:10]}",
        )
        with st.expander("所选文档的完整标识与状态详情"):
            st.code(selected, language=None)
            st.write(DOCUMENT_LABELS[options[selected].status])
            st.caption(f"大小：{options[selected].size / 1024:.1f} KB")
            if options[selected].error:
                st.warning(options[selected].error)
                st.caption(f"错误码：{options[selected].error_code or '未记录'}")
        confirmed = st.checkbox(
            "将所选文档移出活动知识库（原件保留在本地回收目录）", key=f"delete_confirm_{selected}"
        )
        if st.button("移除文档", disabled=not confirmed):
            try:
                with st.spinner("正在更新索引…"):
                    services.knowledge.delete(selected)
                st.session_state["flash"] = "文档已移除，索引已更新；原件可通过 CLI 恢复。"
                st.rerun()
            except AppError as exc:
                st.error(str(exc))
    with st.expander("索引维护"):
        st.write("重新读取全部原件、重新分块并计算向量。模型或分块设置变化后需要执行。")
        st.caption("失败时保留原活动快照。重建可能需要数分钟，期间知识库操作会等待。")
        if st.button("重新建立索引"):
            try:
                with st.spinner("正在准备并发布新索引…"):
                    services.knowledge.rebuild()
                failed = services.knowledge.stats()["failed_documents"]
                st.session_state["flash"] = (
                    f"索引重建完成；{failed} 份失败文档已隔离，请检查文档目录。"
                    if failed
                    else "索引重建完成，全部文档已就绪。"
                )
                st.rerun()
            except AppError as exc:
                st.error(str(exc))


def _lint_rows(lint: dict) -> list[dict]:
    return [
        {
            "检查": check["id"],
            "状态": LINT_STATUS_LABELS[check["status"]],
            "说明": check["message"],
        }
        for check in lint["checks"]
    ]


def _show_lint(lint: dict) -> None:
    rows = _lint_rows(lint)
    st.dataframe(rows, hide_index=True, width="stretch")
    failures = sum(row["状态"] == "失败" for row in rows)
    warnings = sum(row["状态"] == "警告" for row in rows)
    st.caption(f"体检结果：{failures} 项失败，{warnings} 项警告。")


def _show_build_result(services: Services, result: dict) -> None:
    st.divider()
    st.subheader("构建结果")
    st.success("知识包已发布为草稿（自动草稿，未验收）。")
    cols = st.columns(4)
    cols[0].metric("文档", result["stats"]["documents"])
    cols[1].metric("文本块", result["stats"]["chunks"])
    cols[2].metric("被拒绝文件", len(result["failures"]))
    cols[3].metric("体检失败", sum(
        check["status"] == "fail" for check in result["lint"]["checks"]
    ))
    st.caption(
        f"知识包目录：{Path(result['pack_dir']).as_posix()} · "
        f"数据目录：{Path(result['data_dir']).as_posix()}"
    )
    if result["failures"]:
        with st.expander(f"被拒绝的文件（{len(result['failures'])}）"):
            st.dataframe(
                [
                    {
                        "文件": failure["name"],
                        "错误码": failure["error_code"],
                        "说明": failure["error"],
                    }
                    for failure in result["failures"]
                ],
                hide_index=True,
                width="stretch",
            )
    with st.expander("查看体检明细"):
        _show_lint(result["lint"])
    smoke = result["smoke"]["metrics"]
    rate = smoke["document_hit_rate"]
    st.caption(
        f"检索自检：{smoke['document_hits']}/{smoke['cases']} 个查询召回了目标文档"
        + (f"（{rate:.0%}）" if rate is not None else "")
        + "；这只是索引自洽检查，不是回答准确率验收。"
    )
    cols = st.columns(2)
    if cols[0].button("切换到该知识包", type="primary", key="switch_built_pack"):
        activate_pack(result["pack_id"])
    if cols[1].button("关闭结果", key="clear_pack_result"):
        st.session_state.pop("pack_build_result", None)
        st.rerun()
    st.caption("profile/profile.md 是保守模板；P1 不调用 LLM，也不声称已完成人工审核。")


def packs_page(services: Services) -> None:
    st.title("知识包")
    st.caption("把资料整理成可追溯、可体检、可切换的草稿知识包。")
    st.info(
        "建库本身生成确定性草稿；P2 的 profile/评测题起草和在线评测必须先勾选费用确认，"
        "LLM 产物只有人工审核后才生效，系统不会自动宣称验收通过。"
    )
    current = st.session_state.get("active_pack")
    if current:
        st.caption(f"当前工作区：知识包 `{current}`")
    else:
        st.caption("当前工作区：部署默认工作区")

    with st.container(border=True):
        st.subheader("新建知识包")
        with st.form("pack_builder"):
            col1, col2 = st.columns(2)
            pack_id = col1.text_input(
                "知识包 ID",
                key="pack_id_input",
                max_chars=40,
                help="2–40 位小写字母、数字和连字符；创建后不可改名。",
                placeholder="my-knowledge-pack",
            )
            title = col2.text_input(
                "名称",
                key="pack_title_input",
                max_chars=120,
                placeholder="例如：产品手册",
            )
            description = st.text_area(
                "说明", key="pack_description_input", max_chars=1000, height=68
            )
            col1, col2 = st.columns(2)
            intended_use = col1.text_input(
                "预期用途", key="pack_use_input", max_chars=800
            )
            authority_scope = col2.text_input(
                "权威范围", key="pack_scope_input", max_chars=800
            )
            col1, col2 = st.columns(2)
            language = col1.text_input(
                "语言", value="zh", key="pack_language_input", max_chars=20
            )
            license_status = col2.selectbox(
                "资料授权",
                list(LICENSE_LABELS),
                format_func=LICENSE_LABELS.get,
                key="pack_license_input",
            )
            st.caption("同一包内资料应使用相同授权口径；混合授权请分别建包。")
            uploaded = st.file_uploader(
                "资料文件",
                type=UPLOAD_EXTENSIONS,
                accept_multiple_files=True,
                key="pack_uploader",
                help=f"支持 {SUPPORTED_FORMATS_LABEL}；PDF 需文本层，不做 OCR。",
            )
            with st.expander("高级：分块与检索（默认继承当前工作区）"):
                col1, col2 = st.columns(2)
                chunk_size = col1.number_input(
                    "Chunk Size（字符）", 20, 4000, services.settings.chunk_size
                )
                chunk_overlap = col2.number_input(
                    "Chunk Overlap（字符）", 0, 2000, services.settings.chunk_overlap
                )
                col1, col2 = st.columns(2)
                top_k = col1.number_input("Top-K", 1, 20, services.settings.top_k)
                threshold = col2.number_input(
                    "相关性阈值",
                    -1.0,
                    1.0,
                    services.settings.similarity_threshold,
                    step=0.01,
                )
            st.caption("构建期间页面会等待；大资料包需要几分钟。所有产物先写入暂存目录，成功后原子发布。")
            submitted = st.form_submit_button("构建知识包", type="primary")
        if submitted:
            uploads = [(item.name, item.getvalue()) for item in (uploaded or [])]
            if not uploads:
                st.error("请先选择至少一个资料文件。")
            else:
                try:
                    spec = PackSpec(
                        pack_id=pack_id,
                        title=title,
                        description=description,
                        intended_use=intended_use,
                        authority_scope=authority_scope,
                        language=language,
                        license_status=license_status,
                        chunk_size=chunk_size,
                        chunk_overlap=chunk_overlap,
                        top_k=top_k,
                        similarity_threshold=threshold,
                    )
                    with st.spinner("正在解析、分块、建立索引并生成合规草稿…"):
                        result = PackBuilder(
                            services.settings, services.knowledge.embedder
                        ).build(spec, uploads)
                    st.session_state["pack_build_result"] = result.model_dump(mode="json")
                except ValidationError as exc:
                    detail = exc.errors()[0].get("msg", str(exc))
                    st.error(f"知识包参数无效：{detail}")
                except AppError as exc:
                    st.error(str(exc))
                except Exception as exc:
                    st.error(
                        f"构建失败（{type(exc).__name__}）。请检查磁盘权限、模型缓存后重试。"
                    )

    result = st.session_state.get("pack_build_result")
    if result:
        _show_build_result(services, result)

    st.divider()
    st.subheader("已有知识包")
    st.caption("这里只显示带 pack.json 的新知识包；旧版知识包继续使用原启动脚本。")
    catalog = PackCatalog(services.settings)
    entries = catalog.list()
    for warning in catalog.warnings:
        st.warning(warning)
    if not entries:
        st.caption("还没有可管理的知识包。构建成功后会在左侧出现知识包选择器。")
        return
    labels = {
        entry.pack_id: (
            f"{entry.title}（{PACK_STATUS_LABELS[entry.status]}"
            + ("" if entry.has_data else " · 未建索引")
            + "）"
        )
        for entry in entries
    }
    pack_ids = [entry.pack_id for entry in entries]
    if st.session_state.get("pack_manage_select") not in pack_ids:
        st.session_state.pop("pack_manage_select", None)
    selected = st.selectbox(
        "选择知识包",
        pack_ids,
        format_func=lambda value: labels[value],
        key="pack_manage_select",
    )
    entry = catalog.get(selected)
    st.caption(
        f"目录：{entry.pack_dir.as_posix()} · 数据：{entry.data_dir.as_posix()} · "
        f"授权：{LICENSE_LABELS[entry.license_status]} · 分发：{entry.distribution}"
    )
    st.caption(
        f"{PROFILE_STATUS_LABELS[entry.profile_status]} · "
        f"{EVAL_STATUS_LABELS[entry.eval_status]} · "
        f"holdout：{'已使用' if entry.holdout_status == 'used' else '未使用'}"
    )
    pack_settings = None
    try:
        pack_settings = load_pack_settings(selected, base=services.settings)
    except AppError as exc:
        st.error(f"知识包配置无效：{exc}")
    if st.session_state.get("active_pack") == selected:
        st.info("这是当前工作区。")
    cols = st.columns(2)
    if cols[0].button("切换到该知识包", key="switch_existing_pack"):
        activate_pack(selected)
    if cols[1].button("运行体检", key="lint_existing_pack"):
        if pack_settings is None:
            st.error("知识包配置无效，无法体检。")
        else:
            try:
                with st.spinner("正在检查清单、哈希与索引…"):
                    lint = pack_lint(
                        entry.pack_dir,
                        entry.data_dir if entry.has_data else None,
                        secret_values=[services.settings.api_key.get_secret_value()],
                        embedder=BgeEmbedder(pack_settings),
                    )
                st.session_state["pack_lint_result"] = {
                    "pack_id": selected,
                    "lint": lint.model_dump(),
                }
            except AppError as exc:
                st.error(str(exc))
    stored = st.session_state.get("pack_lint_result")
    if stored and stored["pack_id"] == selected:
        _show_lint(stored["lint"])
    if pack_settings is not None:
        if entry.has_data:
            render_pack_studio(services, entry, pack_settings)
        else:
            st.warning("该知识包还没有运行数据；先切换到它并在“知识库管理”重建索引，再审核和评测。")


def settings_page(services: Services) -> None:
    if prefix := st.session_state.pop("settings_feedback_request", None):
        _save_feedback(**saved_feedback(services.knowledge.stats(), prefix))
    if st.session_state.get("settings_pending_rebuild") == str(
        services.settings.data_dir.resolve()
    ):
        st.session_state.pop("settings_pending_rebuild")
        try:
            with st.spinner("设置已保存，正在重建索引…"):
                services.knowledge.rebuild()
            _save_feedback(
                "设置已保存，索引重建完成。",
                index_status=services.knowledge.stats()["index_status"],
            )
        except AppError as exc:
            _save_feedback(
                f"设置已保存；索引重建失败：{exc}",
                warning=True,
                index_status=services.knowledge.stats()["index_status"],
            )
        st.rerun()
    st.title("系统设置")
    st.caption("集中配置模型与检索策略，保持处理流程透明。")
    config = services.settings
    st.info("显式环境变量和 .env 值优先于页面保存值。API Key 仅从环境读取，不在页面展示。")
    locked = environment_owned_keys(config)
    if locked:
        st.caption("由环境配置控制的字段（页面保存不会覆盖）：" + "、".join(sorted(locked)))
    with st.form("settings"):
        st.subheader("即时生效")
        col1, col2 = st.columns(2)
        model = col1.text_input("LLM 模型", config.llm_model, disabled="llm_model" in locked)
        url = col2.text_input(
            "LLM Base URL", config.llm_base_url, disabled="llm_base_url" in locked
        )
        top_k = col1.number_input("Top-K", 1, 20, config.top_k, disabled="top_k" in locked)
        threshold = col2.number_input(
            "相关性阈值",
            -1.0,
            1.0,
            config.similarity_threshold,
            step=0.01,
            disabled="similarity_threshold" in locked,
        )
        timeout = col1.number_input(
            "API 超时（秒）", 1.0, 600.0, config.llm_timeout, disabled="llm_timeout" in locked
        )
        max_tokens = col2.number_input(
            "输出 Token 上限",
            256,
            16384,
            config.llm_max_tokens,
            disabled="llm_max_tokens" in locked,
        )
        lexical_coverage = st.number_input(
            "关键词最小覆盖率（候选门槛）",
            0.01,
            1.0,
            config.lexical_min_coverage,
            disabled="lexical_min_coverage" in locked,
        )
        deepseek = st.checkbox(
            "使用 DeepSeek 非思考模式参数（其他兼容服务请关闭）",
            config.deepseek_mode,
            disabled="deepseek_mode" in locked,
        )
        general = st.checkbox(
            "资料不足时允许通用模型兜底", config.allow_general, disabled="allow_general" in locked
        )
        debug = st.checkbox(
            "聊天中显示检索调试信息", config.show_debug, disabled="show_debug" in locked
        )
        st.caption("阈值是待校准参数，不能直接当作准确率或置信度。证据充分性判断始终启用。")
        st.divider()
        st.subheader("保存后需要重新索引")
        embedding_model = st.text_input(
            "Embedding 模型或本地模型路径",
            config.embedding_model,
            disabled="embedding_model" in locked,
        )
        revision = st.text_input(
            "Embedding revision（可选 Hugging Face commit）",
            config.embedding_revision,
            disabled="embedding_revision" in locked,
        )
        instruction = st.text_input(
            "查询检索指令（只添加到问题）",
            config.query_instruction,
            disabled="query_instruction" in locked,
        )
        col1, col2 = st.columns(2)
        size = col1.number_input(
            "Chunk Size（字符）", 20, 4000, config.chunk_size, disabled="chunk_size" in locked
        )
        overlap = col2.number_input(
            "Chunk Overlap（字符）",
            0,
            2000,
            config.chunk_overlap,
            disabled="chunk_overlap" in locked,
        )
        st.caption(
            "字符数是软上限：按实际 tokenizer 再拆分，保留全文。长问题分段编码后聚合检索向量。"
        )
        save = st.form_submit_button("保存设置", type="primary")
        rebuild = st.form_submit_button("保存并立即重建索引")
        if save or rebuild:
            for key in ("settings_feedback", "focus_settings_feedback"):
                st.session_state.pop(key, None)
            try:
                validated = save_ui_settings(
                    services,
                    {
                        "llm_model": model,
                        "llm_base_url": url,
                        "top_k": top_k,
                        "similarity_threshold": threshold,
                        "llm_timeout": timeout,
                        "llm_max_tokens": max_tokens,
                        "deepseek_mode": deepseek,
                        "allow_general": general,
                        "show_debug": debug,
                        "lexical_min_coverage": lexical_coverage,
                        "embedding_model": embedding_model,
                        "embedding_revision": revision,
                        "query_instruction": instruction,
                        "chunk_size": size,
                        "chunk_overlap": overlap,
                    },
                )
                if rebuild:
                    # The next rerun resolves the newly saved settings through app.resources once.
                    st.session_state["settings_pending_rebuild"] = str(validated.data_dir.resolve())
                else:
                    st.session_state["settings_feedback_request"] = "设置已保存。"
                st.rerun()
            except AppError as exc:
                _save_feedback(str(exc), error=True)
            except ValueError:
                _save_feedback(
                    "设置无效：请检查模型名、URL 和分块范围，Overlap 必须小于 Size。", error=True
                )
    with st.container(key="settings_feedback"):
        feedback = current_feedback(
            st.session_state.get("settings_feedback", {}),
            services.knowledge.stats()["index_status"],
        )
        if feedback:
            st.session_state["settings_feedback"] = feedback
            render = (
                st.error
                if feedback.get("error")
                else st.warning
                if feedback["warning"]
                else st.success
            )
            render(feedback["message"])
            if feedback["requires_reindex"] and st.button("立即重建索引", key="settings_rebuild"):
                try:
                    with st.spinner("正在重建索引…"):
                        services.knowledge.rebuild()
                    _save_feedback(
                        "索引重建完成，当前设置已生效。",
                        index_status=services.knowledge.stats()["index_status"],
                    )
                except AppError as exc:
                    _save_feedback(
                        f"索引重建失败：{exc}",
                        warning=True,
                        index_status=services.knowledge.stats()["index_status"],
                    )
                st.rerun()
        if st.button(
            "恢复页面默认设置", help="清除页面保存的覆盖值；环境变量、API Key、文档和会话保留。"
        ):
            try:
                reset_ui_settings(services)
                st.session_state["settings_feedback_request"] = "已恢复页面默认设置。"
                st.rerun()
            except (AppError, ValueError):
                _save_feedback("恢复默认设置失败，请检查部署配置。原文档与会话保留。", error=True)


def debug_page(services: Services) -> None:
    st.title("检索调试")
    st.caption("观察候选片段、相关性门槛与证据判断，定位 RAG 为什么这样回答。")
    st.info("此页只检索并判断是否可回答，不生成最终答案、不写入聊天记录；证据判断会调用远程 API。")
    with st.form("inspect"):
        question = st.text_area("调试问题", max_chars=2000, placeholder="输入一个希望分析的问题")
        submitted = st.form_submit_button("运行检索与证据判断", type="primary")
    if submitted:
        with st.spinner("正在检索与判断…"):
            st.session_state["inspection"] = services.pipeline.inspect(question)
    inspection = st.session_state.get("inspection")
    if inspection:
        st.caption(f"本次问题：{inspection.question}")
        if inspection.route == "error":
            st.error(inspection.reason)
        inspection_panel(inspection)
        with st.expander("结构化结果"):
            st.json(inspection.model_dump(), expanded=False)
