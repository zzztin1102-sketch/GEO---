"""审核历史记录服务 — 存储、查询、删除审核记录."""

import json
import re
import base64
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from geo_review.utils.time import now as beijing_now

from sqlalchemy import String, select, update, delete, desc, func
from sqlalchemy.ext.asyncio import AsyncSession

from geo_review.history.models import ReviewHistory, ReviewIssue
from geo_review.result.models import ReviewResponse, ReviewStatus


class HistoryService:
    """审核历史记录服务."""

    def __init__(self, async_session):
        self.async_session = async_session

    async def save_review(self, response: ReviewResponse, request_data: Dict[str, Any] = None, batch_id: Optional[str] = None, item_id: Optional[str] = None):
        """保存审核结果到历史记录.

        Args:
            response: 审核响应
            request_data: 原始请求数据（包含content和submission）
            batch_id: 批量任务ID
            item_id: 批次内项目ID
        """
        async with self.async_session() as session:
            result_data = response.model_dump(mode="json")

            # 提取原始内容
            content_text = None
            submission_data = None
            submission_filename = None
            official_urls = None
            if request_data:
                content_text = self._extract_content_text(request_data)
                submission_data = self._extract_submission_data(request_data)
                if submission_data:
                    submission_filename = submission_data.get("filename")
                official_urls = request_data.get("official_urls", [])

            # 智能提取公司名称 / 任务名称：
            # 1) 优先 LLM 响应字段
            # 2) 其次提报表文件中提取的字段
            # 3) 最后从文件名推断（去掉扩展名，剥离"提报表/报告/表格"等关键字）
            response_task_name = response.task_name
            response_company_name = self._extract_company_from_response(result_data)
            submission_task_name = submission_data.get("task_name") if isinstance(submission_data, dict) else None
            submission_company_name = submission_data.get("company_name") if isinstance(submission_data, dict) else None
            inferred_task_name, inferred_company_name = self._infer_names_from_filename(submission_filename)

            final_task_name = response_task_name or submission_task_name or inferred_task_name
            final_company_name = response_company_name or submission_company_name or inferred_company_name

            history = ReviewHistory(
                review_id=str(response.review_id),
                task_name=final_task_name,
                company_name=final_company_name,
                status=response.status.value,
                verdict=response.verdict.value,
                summary=response.summary,
                total_issues=response.stats.total,
                critical_issues=response.stats.by_severity.get("critical", 0),
                major_issues=response.stats.by_severity.get("major", 0),
                minor_issues=response.stats.by_severity.get("minor", 0),
                info_issues=response.stats.by_severity.get("info", 0),
                # 完整内容
                content_text=content_text,
                content_preview=self._get_content_preview_from_text(content_text) if content_text else self._get_content_preview(result_data),
                content_title=self._extract_content_title(content_text, request_data),
                content_source=response.references_used.content_source if response.references_used else None,
                submission_data=submission_data,
                submission_source=response.references_used.submission_source if response.references_used else None,
                official_urls=official_urls or (response.references_used.official_urls_requested if response.references_used else None),
                result_data=result_data,
                error_code=response.error.code if response.error else None,
                error_message=response.error.message if response.error else None,
                reviewed_at=response.reviewed_at,
                duration_ms=response.duration_ms,
                submitted_by=None,
                batch_id=batch_id,
                item_id=item_id,
            )

            session.add(history)
            await session.flush()

            for issue in response.issues:
                db_issue = ReviewIssue(
                    history_id=history.id,
                    issue_id=issue.id,
                    type=issue.type.value,
                    severity=issue.severity.value,
                    title=issue.title,
                    snippet=issue.evidence.snippet if issue.evidence else None,
                    reason=issue.reason if issue.reason else (issue.evidence.reference_detail if issue.evidence else None),
                    suggestion=issue.suggestion,
                    source=issue.evidence.reference_source if issue.evidence else None,
                )
                session.add(db_issue)

            await session.commit()
            await session.refresh(history)

            return history.id

    async def get_review(self, review_id: str) -> Optional[ReviewHistory]:
        """获取单条审核记录."""
        async with self.async_session() as session:
            stmt = select(ReviewHistory).where(
                ReviewHistory.review_id == review_id,
                ReviewHistory.is_deleted == False,
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def get_review_with_issues(self, review_id: str) -> Optional[Dict[str, Any]]:
        """获取审核记录及其问题列表（含完整内容）."""
        async with self.async_session() as session:
            stmt = select(ReviewHistory).where(
                ReviewHistory.review_id == review_id,
                ReviewHistory.is_deleted == False,
            )
            result = await session.execute(stmt)
            history = result.scalar_one_or_none()

            if not history:
                return None

            issues_stmt = select(ReviewIssue).where(
                ReviewIssue.history_id == history.id
            ).order_by(ReviewIssue.created_at)
            issues_result = await session.execute(issues_stmt)
            issues = issues_result.scalars().all()

            return {
                "history": self._history_to_dict(history, include_full_content=True),
                "issues": [self._issue_to_dict(issue) for issue in issues],
            }

    async def list_reviews(
        self,
        page: int = 1,
        page_size: int = 20,
        verdict: Optional[str] = None,
        status: Optional[str] = None,
        company_name: Optional[str] = None,
        task_name: Optional[str] = None,
        content_title: Optional[str] = None,
        submission_name: Optional[str] = None,
        batch_id: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        sort_by: str = "reviewed_at",
        sort_order: str = "desc",
    ) -> Tuple[List[Dict[str, Any]], int]:
        """分页查询审核记录列表."""
        async with self.async_session() as session:
            stmt = select(ReviewHistory).where(ReviewHistory.is_deleted == False)

            if verdict:
                stmt = stmt.where(ReviewHistory.verdict == verdict)
            if status:
                stmt = stmt.where(ReviewHistory.status == status)
            if company_name:
                stmt = stmt.where(ReviewHistory.company_name.ilike(f"%{company_name}%"))
            if task_name:
                stmt = stmt.where(ReviewHistory.task_name.ilike(f"%{task_name}%"))
            if content_title:
                stmt = stmt.where(ReviewHistory.content_title.ilike(f"%{content_title}%"))
            if batch_id:
                stmt = stmt.where(ReviewHistory.batch_id == batch_id)

            # 提报表：用 json_extract 提取 filename 字段后再 LIKE
            # 这样直接在实际字符串值上匹配，不受 JSON ensure_ascii 转义影响
            if submission_name:
                fn_expr = func.json_extract(ReviewHistory.submission_data, '$.filename').cast(String)
                stmt = stmt.where(fn_expr.ilike(f"%{submission_name}%"))

            if start_date:
                try:
                    start_dt = datetime.fromisoformat(start_date)
                    stmt = stmt.where(ReviewHistory.reviewed_at >= start_dt)
                except ValueError:
                    pass

            if end_date:
                try:
                    end_dt = datetime.fromisoformat(end_date)
                    stmt = stmt.where(ReviewHistory.reviewed_at <= end_dt)
                except ValueError:
                    pass

            order_column = getattr(ReviewHistory, sort_by, ReviewHistory.reviewed_at)
            if sort_order == "desc":
                stmt = stmt.order_by(desc(order_column))
            else:
                stmt = stmt.order_by(order_column)

            total_stmt = select(func.count()).select_from(ReviewHistory).where(ReviewHistory.is_deleted == False)
            if verdict:
                total_stmt = total_stmt.where(ReviewHistory.verdict == verdict)
            if status:
                total_stmt = total_stmt.where(ReviewHistory.status == status)
            if company_name:
                total_stmt = total_stmt.where(ReviewHistory.company_name.ilike(f"%{company_name}%"))
            if task_name:
                total_stmt = total_stmt.where(ReviewHistory.task_name.ilike(f"%{task_name}%"))
            if content_title:
                total_stmt = total_stmt.where(ReviewHistory.content_title.ilike(f"%{content_title}%"))
            if submission_name:
                fn_expr = func.json_extract(ReviewHistory.submission_data, '$.filename').cast(String)
                total_stmt = total_stmt.where(fn_expr.ilike(f"%{submission_name}%"))

            total_result = await session.execute(total_stmt)
            total = total_result.scalar() or 0

            offset = (page - 1) * page_size
            stmt = stmt.offset(offset).limit(page_size)

            result = await session.execute(stmt)
            histories = result.scalars().all()

            return [self._history_to_dict(h) for h in histories], total

    async def backfill_inferred_names(self) -> Dict[str, int]:
        """回填历史记录中 task_name / company_name 为空的行。

        通过 filename 推断任务名称和公司名称，幂等：仅更新 NULL 字段，
        多次执行不会覆盖已有值。返回受影响行数统计。
        """
        async with self.async_session() as session:
            stmt = select(ReviewHistory).where(
                ReviewHistory.is_deleted == False,
            )
            result = await session.execute(stmt)
            histories = result.scalars().all()

            updated_task = 0
            updated_company = 0
            for h in histories:
                sub_data = h.submission_data
                if not isinstance(sub_data, dict):
                    continue
                filename = sub_data.get("filename")
                inferred_task, inferred_company = self._infer_names_from_filename(filename)
                # 同时考虑 submission_data 自带的 task_name / company_name
                sub_task = sub_data.get("task_name")
                sub_company = sub_data.get("company_name")
                if isinstance(sub_task, str) and sub_task.strip():
                    new_task = sub_task.strip()
                elif not h.task_name and inferred_task:
                    new_task = inferred_task
                else:
                    new_task = None
                if isinstance(sub_company, str) and sub_company.strip():
                    new_company = sub_company.strip()
                elif not h.company_name and inferred_company:
                    new_company = inferred_company
                else:
                    new_company = None

                changed = False
                if new_task and h.task_name != new_task:
                    h.task_name = new_task
                    updated_task += 1
                    changed = True
                if new_company and h.company_name != new_company:
                    h.company_name = new_company
                    updated_company += 1
                    changed = True
                if changed:
                    h.updated_at = beijing_now()

            if updated_task or updated_company:
                await session.commit()

            return {"updated_task_name": updated_task, "updated_company_name": updated_company, "scanned": len(histories)}

    async def delete_review(self, review_id: str) -> bool:
        """软删除审核记录."""
        async with self.async_session() as session:
            stmt = update(ReviewHistory).where(
                ReviewHistory.review_id == review_id,
                ReviewHistory.is_deleted == False,
            ).values(is_deleted=True, updated_at=beijing_now())

            result = await session.execute(stmt)
            await session.commit()

            return result.rowcount > 0

    async def update_human_review(
        self, review_id: str, human_review_data: Dict[str, Any]
    ) -> bool:
        """保存人工复核结果.

        Args:
            review_id: 审核记录ID
            human_review_data: 人工复核数据，包含:
                - status: confirmed / rejected / false_positive / revised
                - reviewer: 复核人
                - comment: 复核备注
                - issue_actions: 各问题的处理动作 {issue_id: action}
        """
        async with self.async_session() as session:
            # 读取现有记录
            stmt = select(ReviewHistory).where(
                ReviewHistory.review_id == review_id,
                ReviewHistory.is_deleted == False,
            )
            result = await session.execute(stmt)
            history = result.scalar_one_or_none()
            if not history:
                return False

            # 合并人工复核数据到 result_data
            import json as _json
            result_data = _json.loads(history.result_data) if isinstance(history.result_data, str) else (history.result_data or {})
            result_data["human_review"] = {
                "status": human_review_data.get("status", "pending"),
                "reviewer": human_review_data.get("reviewer", ""),
                "comment": human_review_data.get("comment", ""),
                "issue_actions": human_review_data.get("issue_actions", {}),
                "reviewed_at": beijing_now().isoformat(),
            }

            # 更新记录
            update_stmt = update(ReviewHistory).where(
                ReviewHistory.review_id == review_id,
            ).values(
                result_data=_json.dumps(result_data, ensure_ascii=False),
                updated_at=beijing_now(),
            )
            await session.execute(update_stmt)
            await session.commit()
            return True

    async def batch_delete(self, review_ids: List[str]) -> int:
        """批量软删除审核记录."""
        async with self.async_session() as session:
            stmt = update(ReviewHistory).where(
                ReviewHistory.review_id.in_(review_ids),
                ReviewHistory.is_deleted == False,
            ).values(is_deleted=True, updated_at=beijing_now())

            result = await session.execute(stmt)
            await session.commit()

            return result.rowcount

    async def get_batch_results(self, batch_id: str) -> List[Dict[str, Any]]:
        """获取批量任务的所有审核结果."""
        async with self.async_session() as session:
            stmt = select(ReviewHistory).where(
                ReviewHistory.batch_id == batch_id,
                ReviewHistory.is_deleted == False,
            ).order_by(ReviewHistory.reviewed_at)

            result = await session.execute(stmt)
            histories = result.scalars().all()

            return [self._history_to_dict(h) for h in histories]

    async def get_statistics(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """获取审核统计数据."""
        async with self.async_session() as session:
            stmt = select(ReviewHistory).where(ReviewHistory.is_deleted == False)

            if start_date:
                try:
                    start_dt = datetime.fromisoformat(start_date)
                    stmt = stmt.where(ReviewHistory.reviewed_at >= start_dt)
                except ValueError:
                    pass

            if end_date:
                try:
                    end_dt = datetime.fromisoformat(end_date)
                    stmt = stmt.where(ReviewHistory.reviewed_at <= end_dt)
                except ValueError:
                    pass

            result = await session.execute(stmt)
            histories = result.scalars().all()

            total = len(histories)
            pass_count = sum(1 for h in histories if h.verdict == "pass")
            revise_count = sum(1 for h in histories if h.verdict == "revise")
            reject_count = sum(1 for h in histories if h.verdict == "reject")
            failed_count = sum(1 for h in histories if h.status == "failed")

            total_issues = sum(h.total_issues or 0 for h in histories)
            avg_issues = total_issues / total if total > 0 else 0

            avg_duration = sum(h.duration_ms or 0 for h in histories) / total if total > 0 else 0

            # 按严重程度统计
            critical_count = sum(h.critical_issues or 0 for h in histories)
            major_count = sum(h.major_issues or 0 for h in histories)
            minor_count = sum(h.minor_issues or 0 for h in histories)
            info_count = max(total_issues - critical_count - major_count - minor_count, 0)

            return {
                "total_reviews": total,
                "pass_count": pass_count,
                "revise_count": revise_count,
                "reject_count": reject_count,
                "failed_count": failed_count,
                "pass_rate": pass_count / total * 100 if total > 0 else 0,
                "total_issues": total_issues,
                "avg_issues_per_review": avg_issues,
                "avg_duration_ms": avg_duration,
                "critical_count": critical_count,
                "by_severity": {
                    "critical": critical_count,
                    "major": major_count,
                    "minor": minor_count,
                    "info": info_count,
                },
                "by_verdict": {
                    "pass": pass_count,
                    "revise": revise_count,
                    "reject": reject_count,
                },
            }

    def _extract_company_from_response(self, result_data: Dict[str, Any]) -> Optional[str]:
        """从响应中提取公司名称（兜底，目前 LLM 响应内无此字段，未来可扩展）."""
        if not result_data:
            return None
        submission = result_data.get("submission")
        if isinstance(submission, dict):
            v = submission.get("company_name")
            return v if isinstance(v, str) and v.strip() else None
        return None

    # 中文 → 推断关键词映射
    _FILENAME_TASK_KEYWORDS = (
        "提报表", "表格", "申报", "备案", "报表", "数据表",
        "brief", "Brief", "BRIEF",
    )
    _FILENAME_DATE_PATTERN = r"[\d]{1,2}[._-][\d]{1,2}([._-][\d]{1,4})?|[\d]{4}[._-][\d]{1,2}[._-][\d]{1,2}"
    _FILENAME_VERSION_PATTERN = r"v\d+(\.\d+)?|最新版|新版|旧版|初版|终版|终稿|已更新|已定稿|已审核|草稿"

    def _infer_names_from_filename(self, filename: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
        """从文件名中推断 (task_name, company_name)。

        启发式逻辑：
        1) 去掉扩展名（.xlsx/.xls/.csv/.json/.txt/.docx 等）
        2) 去掉日期/版本号后缀
        3) 拆分剩余部分，前段为公司名，后段含"提报表/表格"等关键字则为任务描述
        4) 如无法拆分，整个文件名去"提报表/表格"等关键字后作为公司名

        Examples:
            "永安期货提报表7.23已更新.xlsx"
                → task_name=None, company_name="永安期货"
            "约牛.xlsx"
                → task_name=None, company_name="约牛"
            "中信建投期货-品牌审核表2024-12-01.xlsx"
                → task_name="品牌审核表", company_name="中信建投期货"
        """
        if not filename or not isinstance(filename, str):
            return None, None
        try:
            # 1. 去掉扩展名
            stem = re.sub(r"\.[A-Za-z0-9]+$", "", filename).strip()
            if not stem:
                return None, None

            # 2. 去掉日期/版本后缀
            cleaned = re.sub(self._FILENAME_DATE_PATTERN, "", stem)
            cleaned = re.sub(self._FILENAME_VERSION_PATTERN, "", cleaned, flags=re.IGNORECASE)
            cleaned = cleaned.strip().strip("-_ ").strip()
            if not cleaned:
                cleaned = stem

            # 3. 查找任务关键词位置，按位置切分
            task_keyword_match = None
            task_kw_lower = [k.lower() for k in self._FILENAME_TASK_KEYWORDS]
            for kw in self._FILENAME_TASK_KEYWORDS:
                idx = cleaned.find(kw)
                if idx >= 0:
                    task_keyword_match = (idx, kw, len(kw))
                    break
            # 找不到关键字时尝试用常见分隔符（- / _）
            if task_keyword_match is None:
                for sep in (" - ", "-", "_", "／", "/"):
                    if sep in cleaned:
                        left, _, right = cleaned.partition(sep)
                        left = left.strip()
                        right = re.sub(self._FILENAME_VERSION_PATTERN, "", right, flags=re.IGNORECASE).strip()
                        if left and right:
                            return right, left

            if task_keyword_match is None:
                # 整个文件名就是公司名（关键词如"提报表"已被清理掉）
                # 如果残留包含"提报表"等词再去掉
                for kw in self._FILENAME_TASK_KEYWORDS:
                    if kw in cleaned:
                        cleaned = cleaned.replace(kw, "").strip()
                return None, cleaned or stem

            idx, kw, klen = task_keyword_match
            company_part = cleaned[:idx].strip().strip("-_ ")
            task_part_raw = cleaned[idx + klen:].strip().strip("-_ ")
            task_part = re.sub(self._FILENAME_VERSION_PATTERN, "", task_part_raw, flags=re.IGNORECASE).strip().strip("-_ ")
            task_name = task_part if task_part else kw
            company_name = company_part if company_part else stem
            return (task_name or None), (company_name or None)
        except Exception:
            return None, None

    def _extract_content_title(self, content_text: Optional[str], request_data: Optional[Dict[str, Any]]) -> Optional[str]:
        """从正文内容中提取标题（首行或前50字）."""
        # 优先使用文档链接模式下的标题
        if request_data:
            metadata = request_data.get("metadata", {})
            if isinstance(metadata, dict):
                doc_title = metadata.get("document_title")
                if doc_title:
                    return doc_title[:60] + ("..." if len(doc_title) > 60 else "")
        if content_text and isinstance(content_text, str):
            lines = [line.strip() for line in content_text.split('\n') if line.strip()]
            if lines:
                first_line = lines[0]
                if len(first_line) > 60:
                    return first_line[:57] + "..."
                return first_line
        if request_data:
            content = request_data.get("content", {})
            filename = content.get("filename") or content.get("file", {}).get("filename")
            if filename:
                return filename
        return None

    def _get_submission_summary(self, submission_data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """从提报表数据中提取摘要信息（用于列表展示）."""
        if not submission_data or not isinstance(submission_data, dict):
            return None
        summary = {}
        if submission_data.get("company_name"):
            summary["company_name"] = submission_data["company_name"]
        if submission_data.get("task_name"):
            summary["task_name"] = submission_data["task_name"]
        if submission_data.get("product_name"):
            summary["product_name"] = submission_data["product_name"]
        if submission_data.get("industry"):
            summary["industry"] = submission_data["industry"]
        if submission_data.get("official_website_url"):
            summary["official_website_url"] = submission_data["official_website_url"]
        if submission_data.get("filename"):
            summary["filename"] = submission_data["filename"]
        return summary if summary else None

    def _extract_content_text(self, request_data: Dict[str, Any]) -> Optional[str]:
        """从请求数据中提取正文内容."""
        if not request_data:
            return None
        content = request_data.get("content", {})
        if content.get("input_type") == "text":
            return content.get("text", "")
        if content.get("input_type") == "file":
            file_info = content.get("file", {})
            content_b64 = file_info.get("content_base64")
            filename = file_info.get("filename")
            fmt = file_info.get("format")
            if content_b64 and filename:
                try:
                    from geo_review.parsers.content import ContentParser
                    file_bytes = base64.b64decode(content_b64)
                    parsed = ContentParser.parse(
                        file_bytes,
                        filename=filename,
                        format_hint=fmt,
                        max_length=10000,
                    )
                    return parsed.text
                except Exception:
                    pass
        return None

    def _extract_submission_data(self, request_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """从请求数据中提取提报表数据."""
        if not request_data:
            return None
        submission = request_data.get("submission", {})
        if submission.get("input_type") == "json":
            return submission.get("data", {})
        elif submission.get("input_type") == "text":
            return {"text": submission.get("text", "")}
        elif submission.get("input_type") == "file":
            file_info = submission.get("file", {})
            result = {
                "filename": file_info.get("filename"),
                "format": file_info.get("format"),
            }
            override_data = submission.get("override_data")
            if override_data and isinstance(override_data, dict):
                result.update(override_data)
            content_b64 = file_info.get("content_base64")
            filename = file_info.get("filename")
            fmt = file_info.get("format")
            if content_b64 and filename:
                try:
                    from geo_review.parsers.submission import SubmissionParser
                    file_bytes = base64.b64decode(content_b64)
                    parsed = SubmissionParser.parse(
                        file_bytes,
                        filename=filename,
                        format_hint=fmt,
                    )
                    if parsed and parsed.data:
                        for key in ["company_name", "task_name", "product_name", "industry", "official_website_url"]:
                            if key in parsed.data and not result.get(key):
                                result[key] = parsed.data[key]
                except Exception:
                    pass
            return result if any(result.values()) else None
        return None

    def _get_content_preview(self, result_data: Dict[str, Any]) -> Optional[str]:
        """获取内容预览（前200字符）."""
        if result_data:
            content = result_data.get("content")
            if content and isinstance(content, str):
                return content[:200]
        return None

    def _get_content_preview_from_text(self, text: str) -> Optional[str]:
        """从文本获取预览（前500字符）."""
        if text and isinstance(text, str):
            return text[:500]
        return None

    def _history_to_dict(self, history: ReviewHistory, include_full_content: bool = False) -> Dict[str, Any]:
        """将历史记录转换为字典."""
        # 从 submission_data 补回 filename 用于 UI 展示与"提报表"搜索
        submission_filename = None
        submission_data_dict = history.submission_data
        if isinstance(submission_data_dict, dict):
            submission_filename = submission_data_dict.get("filename")

        result = {
            "id": history.id,
            "review_id": history.review_id,
            "task_name": history.task_name,
            "company_name": history.company_name,
            "status": history.status,
            "verdict": history.verdict,
            "summary": history.summary,
            "total_issues": history.total_issues,
            "critical_issues": history.critical_issues,
            "major_issues": history.major_issues,
            "minor_issues": history.minor_issues,
            "info_issues": history.info_issues,
            "content_title": history.content_title,
            "content_preview": history.content_preview,
            "content_source": history.content_source,
            "submission_source": history.submission_source,
            "submission_filename": submission_filename,
            "submission_summary": self._get_submission_summary(history.submission_data),
            "official_urls": history.official_urls,
            "error_code": history.error_code,
            "error_message": history.error_message,
            "reviewed_at": history.reviewed_at.isoformat() if history.reviewed_at else None,
            "duration_ms": history.duration_ms,
            "submitted_by": history.submitted_by,
            "batch_id": history.batch_id,
            "item_id": history.item_id,
            "created_at": history.created_at.isoformat() if history.created_at else None,
        }

        if include_full_content:
            result["content_text"] = history.content_text
            result["submission_data"] = history.submission_data
            result["result_data"] = history.result_data

        return result

    def _issue_to_dict(self, issue: ReviewIssue) -> Dict[str, Any]:
        """将问题记录转换为字典."""
        return {
            "id": issue.id,
            "issue_id": issue.issue_id,
            "type": issue.type,
            "severity": issue.severity,
            "title": issue.title,
            "snippet": issue.snippet,
            "reason": issue.reason,
            "suggestion": issue.suggestion,
            "source": issue.source,
            "created_at": issue.created_at.isoformat() if issue.created_at else None,
        }
