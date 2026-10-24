"""Knowledge-pack review studio: drafts, review gates, frozen eval and reports."""

import hashlib
import json
from pathlib import Path

import streamlit as st

from rag_workbench.application.knowledge import KnowledgeService
from rag_workbench.application.pack_authoring import PackAuthoring
from rag_workbench.application.pack_evaluation import PackEvaluation
from rag_workbench.application.pack_review import PackReview
from rag_workbench.application.packs import PackSummary
from rag_workbench.bootstrap import Services
from rag_workbench.config import Settings
from rag_workbench.domain.errors import AppError
from rag_workbench.infrastructure.embedding import BgeEmbedder

COST_LABEL = "我确认会调用远程 LLM，可能产生 API 费用"
PROFILE_STATUS = {"draft": "profile 草稿", "reviewed": "profile 已审核"}
EVAL_STATUS = {"none": "无评测集", "candidate": "评测题候选中", "frozen": "评测集已冻结"}


def _knowledge(services: Services, pack_settings: Settings) -> KnowledgeService:
    if pack_settings.data_dir.resolve() == services.settings.data_dir.resolve():
        return services.knowledge
    return KnowledgeService(pack_settings, BgeEmbedder(pack_settings))


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _flash(key: str, message: str, *, error: bool = False) -> None:
    st.session_state[key] = {"message": message, "error": error}


def _show_flash(key: str) -> None:
    value = st.session_state.pop(key, None)
    if not value:
        return
    (st.error if value["error"] else st.success)(value["message"])


def _load_freeze(pack_dir: Path):
    try:
        return PackReview.read_current_freeze(pack_dir)
    except AppError:
        return None


def render_pack_studio(services: Services, entry: PackSummary, pack_settings: Settings) -> None:
    knowledge = _knowledge(services, pack_settings)
    st.divider()
    st.subheader("审核与评测")
    st.caption(
        f"{PROFILE_STATUS[entry.profile_status]} · {EVAL_STATUS[entry.eval_status]} · "
        f"holdout：{'已使用' if entry.holdout_status == 'used' else '未使用'}"
    )
    tabs = st.tabs(["Profile", "评测集", "评测报告"])
    with tabs[0]:
        _profile_tab(services, entry, pack_settings, knowledge)
    with tabs[1]:
        _eval_tab(services, entry, pack_settings, knowledge)
    with tabs[2]:
        _report_tab(services, entry, pack_settings, knowledge)


def _profile_tab(
    services: Services, entry: PackSummary, pack_settings: Settings, knowledge: KnowledgeService
) -> None:
    pack_dir = entry.pack_dir
    flash_key = f"profile_flash_{entry.pack_id}"
    _show_flash(flash_key)
    current_path = pack_dir / "profile" / "profile.md"
    draft_path = pack_dir / "profile" / "profile.draft.md"
    draft_meta = _read_json(pack_dir / "profile" / "profile.draft.meta.json")
    current = _read(current_path)
    st.caption(
        "profile 会参与证据判断和回答生成。采用草案或保存编辑都会把状态标记为已人工审核；"
        "远程模型会收到资料摘录。"
    )
    cost_ack = st.checkbox(COST_LABEL, key=f"cost_profile_{entry.pack_id}")
    if st.button(
        "用 LLM 起草 profile",
        key=f"draft_profile_{entry.pack_id}",
        disabled=not cost_ack,
    ):
        try:
            with st.spinner("正在请求模型起草 profile…"):
                PackAuthoring(
                    pack_settings, knowledge, services.pipeline.provider
                ).draft_profile(pack_dir, confirm_cost=True)
            _flash(flash_key, "profile 草案已生成，尚未替换当前 profile。")
        except AppError as exc:
            _flash(flash_key, str(exc), error=True)
        st.rerun()

    if draft_path.is_file():
        st.markdown("#### LLM 草案")
        draft_text = _read(draft_path)
        edited = st.text_area(
            "草案内容（可编辑）",
            value=draft_text,
            height=280,
            key=f"draft_edit_{entry.pack_id}_{hashlib.sha256(draft_text.encode()).hexdigest()[:8]}",
        )
        if draft_meta:
            st.caption(
                f"模型：{draft_meta.get('model', '-')} · prompt："
                f"{draft_meta.get('prompt_version', '-')} · 输入哈希："
                f"{str(draft_meta.get('input_sha256', '-'))[:12]}…"
            )
        cols = st.columns(2)
        if cols[0].button("采用并标记已审核", key=f"adopt_draft_{entry.pack_id}"):
            try:
                if edited.strip() == draft_text.strip():
                    PackReview.adopt_profile(pack_dir, accept_draft=True)
                else:
                    PackReview.adopt_profile(pack_dir, content=edited, source="manual")
                st.cache_resource.clear()
                _flash(flash_key, "已采用 profile 草案并标记为已审核。")
            except AppError as exc:
                _flash(flash_key, str(exc), error=True)
            st.rerun()
        if cols[1].button("丢弃草案", key=f"discard_draft_{entry.pack_id}"):
            draft_path.unlink(missing_ok=True)
            (pack_dir / "profile" / "profile.draft.meta.json").unlink(missing_ok=True)
            _flash(flash_key, "已丢弃 profile 草案。")
            st.rerun()

    st.markdown("#### 当前 profile")
    st.caption("也可以直接手工修改；保存会把 profile 标记为已审核。")
    current_edit = st.text_area(
        "当前 profile 内容",
        value=current,
        height=280,
        key=f"current_profile_{entry.pack_id}_{hashlib.sha256(current.encode()).hexdigest()[:8]}",
    )
    if st.button("保存编辑并标记已审核", key=f"save_profile_{entry.pack_id}"):
        try:
            PackReview.adopt_profile(pack_dir, content=current_edit, source="manual")
            st.cache_resource.clear()
            _flash(flash_key, "profile 已保存并标记为已审核。")
        except AppError as exc:
            _flash(flash_key, str(exc), error=True)
        st.rerun()


def _eval_tab(
    services: Services, entry: PackSummary, pack_settings: Settings, knowledge: KnowledgeService
) -> None:
    pack_dir = entry.pack_dir
    flash_key = f"eval_flash_{entry.pack_id}"
    _show_flash(flash_key)
    freeze = _load_freeze(pack_dir)
    if freeze:
        st.caption(
            f"当前冻结版本：{freeze.version} · dev {freeze.counts.get('dev', 0)} 题 · "
            f"holdout {freeze.counts.get('holdout', 0)} 题 · "
            f"holdout {'已使用' if freeze.holdout_status == 'used' else '未使用'}"
        )
        for warning in freeze.warnings:
            st.warning(warning)
    st.caption(
        "LLM 只起草候选；必须人工审核、分流并冻结。正例要有真实 chunk 锚点，"
        "负例不得被检索门槛放行。"
    )
    cost_ack = st.checkbox(COST_LABEL, key=f"cost_eval_{entry.pack_id}")
    count = st.number_input(
        "起草题目数量",
        min_value=6,
        max_value=20,
        value=12,
        key=f"candidate_count_{entry.pack_id}",
    )
    if st.button(
        "用 LLM 起草评测题",
        key=f"draft_eval_{entry.pack_id}",
        disabled=not cost_ack,
    ):
        try:
            with st.spinner("正在请求模型起草评测题…"):
                candidates = PackAuthoring(
                    pack_settings, knowledge, services.pipeline.provider
                ).generate_candidates(
                    pack_dir, count=int(count), confirm_cost=True
                )
            _flash(flash_key, f"已生成 {len(candidates)} 道候选评测题，尚未冻结。")
        except AppError as exc:
            _flash(flash_key, str(exc), error=True)
        st.rerun()

    try:
        candidates = PackReview.read_candidates(pack_dir)
    except AppError as exc:
        st.error(str(exc))
        return
    if not candidates:
        st.info("还没有候选评测题；可以先生成，或稍后手工准备。")
        return
    chunks = {chunk.id: chunk for chunk in knowledge.repo.chunks()}
    st.caption("先保存审核结果，再冻结。冻结前请确认 profile 已审核。")
    with st.form(f"review_candidates_{entry.pack_id}"):
        decisions = {}
        for candidate in candidates:
            with st.expander(f"{candidate.id} · {candidate.question[:48]}"):
                st.caption(
                    f"类别：{candidate.category} · 可达："
                    f"{'是' if candidate.reachable else '否'} · "
                    f"负例风险：{'是' if candidate.negative_risk else '否'}"
                )
                question = st.text_input(
                    "问题",
                    value=candidate.question,
                    key=f"review_question_{entry.pack_id}_{candidate.id}",
                )
                cols = st.columns(2)
                status = cols[0].selectbox(
                    "审核",
                    ["pending", "approved", "rejected"],
                    index=["pending", "approved", "rejected"].index(candidate.status),
                    key=f"review_status_{entry.pack_id}_{candidate.id}",
                )
                split = cols[1].selectbox(
                    "分流",
                    ["unassigned", "dev", "holdout"],
                    index=["unassigned", "dev", "holdout"].index(candidate.split),
                    key=f"review_split_{entry.pack_id}_{candidate.id}",
                )
                reference = st.text_area(
                    "参考要点（人工核对）",
                    value=candidate.reference_answer,
                    height=70,
                    key=f"review_reference_{entry.pack_id}_{candidate.id}",
                )
                evidence = []
                for chunk_id in candidate.relevant_chunk_ids:
                    chunk = chunks.get(chunk_id)
                    if chunk is None:
                        evidence.append(f"{chunk_id}（索引中缺失）")
                    else:
                        evidence.append(
                            f"{chunk.document_name} #{chunk.ordinal}：{chunk.text[:80]}"
                        )
                if evidence:
                    st.caption("证据：" + " ｜ ".join(evidence))
                for warning in candidate.warnings:
                    st.warning(warning)
                decisions[candidate.id] = {
                    "status": status,
                    "split": split,
                    "question": question,
                    "reference_answer": reference,
                }
        if st.form_submit_button("保存审核结果"):
            try:
                PackReview.save_review(pack_dir, decisions)
                _flash(flash_key, "审核结果已保存。")
            except AppError as exc:
                _flash(flash_key, str(exc), error=True)
            st.rerun()

    if st.button("冻结评测集", key=f"freeze_eval_{entry.pack_id}"):
        try:
            with st.spinner("正在校验并冻结评测集…"):
                result = PackReview.freeze_eval(pack_dir, knowledge)
            _flash(
                flash_key,
                f"已冻结 {result.version}：dev {result.counts['dev']} 题 / "
                f"holdout {result.counts['holdout']} 题。",
            )
        except AppError as exc:
            _flash(flash_key, str(exc), error=True)
        st.rerun()


def _report_tab(
    services: Services, entry: PackSummary, pack_settings: Settings, knowledge: KnowledgeService
) -> None:
    pack_dir = entry.pack_dir
    flash_key = f"report_flash_{entry.pack_id}"
    _show_flash(flash_key)
    freeze = _load_freeze(pack_dir)
    if freeze is None:
        st.info("还没有冻结的评测集；先到“评测集”标签审核并冻结。")
        return
    if (
        _sha256_file(pack_dir / "profile" / "profile.md") != freeze.profile_sha256
        or _sha256_file(pack_dir / "settings.json") != freeze.settings_sha256
        or not freeze.corpus_sha256
        or _sha256_file(pack_dir / "generated_manifest.jsonl") != freeze.corpus_sha256
    ):
        st.error(
            "profile、settings 或正文清单在冻结后已变化；"
            "需要重新冻结评测集，评测会被阻止。"
        )
    st.caption(
        f"当前版本 {freeze.version} · holdout "
        f"{'已使用' if freeze.holdout_status == 'used' else '未使用'}"
    )
    if freeze.holdout_status == "used":
        st.warning("该 holdout 已使用；再次点击只允许 dev 评测。新验收需要新的冻结版本。")
    split = st.radio(
        "分流",
        ["dev", "holdout"],
        horizontal=True,
        key=f"report_split_{entry.pack_id}",
    )
    holdout_ack = False
    if split == "holdout":
        holdout_ack = st.checkbox(
            "我确认运行 holdout 会消耗一次性留出资格",
            key=f"holdout_ack_{entry.pack_id}",
            disabled=freeze.holdout_status == "used",
        )
    online_ack = st.checkbox(COST_LABEL, key=f"cost_report_{entry.pack_id}")
    cols = st.columns(2)
    if cols[0].button(
        "运行离线评测",
        key=f"run_offline_{entry.pack_id}",
        disabled=(split == "holdout" and not holdout_ack)
        or (split == "holdout" and freeze.holdout_status == "used"),
    ):
        _run(
            services,
            entry,
            pack_settings,
            knowledge,
            split=split,
            online=False,
            confirm_cost=False,
            confirm_holdout=holdout_ack,
        )
    if cols[1].button(
        "运行在线评测",
        key=f"run_online_{entry.pack_id}",
        disabled=not online_ack
        or (split == "holdout" and not holdout_ack)
        or (split == "holdout" and freeze.holdout_status == "used"),
    ):
        _run(
            services,
            entry,
            pack_settings,
            knowledge,
            split=split,
            online=True,
            confirm_cost=True,
            confirm_holdout=holdout_ack,
        )

    runs_root = pack_dir / "eval" / "runs"
    report_paths = sorted(
        runs_root.glob("*/*/report.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not report_paths:
        return
    st.markdown("#### 最近运行")
    labels = {
        path.parent.relative_to(runs_root).as_posix(): path.parent.relative_to(
            runs_root
        ).as_posix()
        for path in report_paths[:10]
    }
    pending = st.session_state.pop(f"pending_report_run_{entry.pack_id}", None)
    if pending in labels:
        st.session_state[f"report_run_{entry.pack_id}"] = pending
    if st.session_state.get(f"report_run_{entry.pack_id}") not in labels:
        st.session_state.pop(f"report_run_{entry.pack_id}", None)
    selected = st.selectbox(
        "选择运行记录",
        list(labels),
        format_func=labels.get,
        key=f"report_run_{entry.pack_id}",
    )
    report = _read_json(runs_root / selected / "report.json")
    if not report:
        st.error("运行报告无法读取。")
        return
    metrics = report.get("metrics", {})
    cols = st.columns(4)
    cols[0].metric("用例", metrics.get("cases", "-"))
    cols[1].metric("检索命中", _fmt(metrics.get("retrieval_hit_rate")))
    cols[2].metric("路由准确", _fmt(metrics.get("routing_accuracy")))
    cols[3].metric("服务错误", metrics.get("service_errors", "-"))
    st.caption(
        f"在线：{'是' if report.get('online') else '否'} · "
        f"holdout 消耗：{'是' if report.get('holdout_consumed') else '否'} · "
        f"{report.get('created_at', '')}"
    )
    diagnosis = report.get("diagnosis", {})
    counts = diagnosis.get("counts", {})
    if counts:
        st.dataframe(
            [{"类别": key, "数量": value} for key, value in counts.items()],
            hide_index=True,
            width="stretch",
        )
    for item in diagnosis.get("recommendations", []):
        st.caption(f"- {item}")
    cases = diagnosis.get("cases", [])
    if cases:
        st.dataframe(
            [
                {
                    "题号": case.get("id"),
                    "类别": case.get("category"),
                    "期望": case.get("expected_mode"),
                    "实际": case.get("mode") or "-",
                    "错误码": case.get("error_code") or "-",
                }
                for case in cases
            ],
            hide_index=True,
            width="stretch",
        )
    st.caption(f"报告：{(runs_root / selected / 'report.json').as_posix()}")


def _run(
    services: Services,
    entry: PackSummary,
    pack_settings: Settings,
    knowledge: KnowledgeService,
    *,
    split: str,
    online: bool,
    confirm_cost: bool,
    confirm_holdout: bool,
) -> None:
    flash_key = f"report_flash_{entry.pack_id}"
    try:
        with st.spinner("正在运行评测；在线模式会逐题调用模型…"):
            run = PackEvaluation(
                pack_settings, knowledge, services.pipeline.provider, entry.pack_dir
            ).run(
                split=split,
                online=online,
                confirm_cost=confirm_cost,
                confirm_holdout=confirm_holdout,
            )
        runs_root = entry.pack_dir / "eval" / "runs"
        st.session_state[f"pending_report_run_{entry.pack_id}"] = (
            run.report_path.parent.relative_to(runs_root).as_posix()
        )
        _flash(
            flash_key,
            f"评测完成：{run.split}，{run.metrics.get('cases', 0)} 题；报告已写入。",
        )
    except AppError as exc:
        _flash(flash_key, str(exc), error=True)
    st.rerun()


def _fmt(value) -> str:
    if value is None:
        return "未测"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _sha256_file(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""
