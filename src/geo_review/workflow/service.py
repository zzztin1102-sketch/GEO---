"""审核流程服务 — 状态机管理、人工复核、审核意见."""

import uuid
from typing import Any, Dict, List, Optional

from geo_review.utils.time import now as beijing_now

from geo_review.workflow.models import (
    ReviewWorkflowStatus,
    ReviewComment,
    ReviewAuditLog,
    WorkflowSummary,
)


class WorkflowService:
    """审核流程管理服务.

    管理审核记录的全生命周期状态流转：
        待审核 -> 审核中 -> 审核完成 -> 人工复核 -> 通过/不通过 -> 归档

    数据存储：基于内存（与 BatchReviewService 一致，服务重启后丢失）
    如需持久化，可将数据存入数据库。
    """

    # 状态流转图：{当前状态: {操作: 目标状态}}
    _TRANSITIONS: Dict[str, Dict[str, str]] = {
        ReviewWorkflowStatus.PENDING.value: {},
        ReviewWorkflowStatus.PROCESSING.value: {},
        ReviewWorkflowStatus.COMPLETED.value: {
            "review": ReviewWorkflowStatus.REVIEWING.value,
            "archive": ReviewWorkflowStatus.ARCHIVED.value,
        },
        ReviewWorkflowStatus.FAILED.value: {
            "revise": ReviewWorkflowStatus.REVISING.value,
        },
        ReviewWorkflowStatus.REVIEWING.value: {
            "approve": ReviewWorkflowStatus.APPROVED.value,
            "reject": ReviewWorkflowStatus.REJECTED.value,
        },
        ReviewWorkflowStatus.APPROVED.value: {
            "archive": ReviewWorkflowStatus.ARCHIVED.value,
        },
        ReviewWorkflowStatus.REJECTED.value: {
            "revise": ReviewWorkflowStatus.REVISING.value,
        },
        ReviewWorkflowStatus.REVISING.value: {
            "submit": ReviewWorkflowStatus.PENDING.value,
        },
        ReviewWorkflowStatus.ARCHIVED.value: {},
    }

    _STATUS_DISPLAY = {
        ReviewWorkflowStatus.PENDING.value: "待审核",
        ReviewWorkflowStatus.PROCESSING.value: "审核中",
        ReviewWorkflowStatus.COMPLETED.value: "审核完成",
        ReviewWorkflowStatus.FAILED.value: "审核失败",
        ReviewWorkflowStatus.REVIEWING.value: "人工复核中",
        ReviewWorkflowStatus.APPROVED.value: "复核通过",
        ReviewWorkflowStatus.REJECTED.value: "复核不通过",
        ReviewWorkflowStatus.REVISING.value: "返回修改中",
        ReviewWorkflowStatus.ARCHIVED.value: "已归档",
    }

    def __init__(self):
        # review_id -> status
        self._statuses: Dict[str, str] = {}
        # review_id -> List[ReviewComment]
        self._comments: Dict[str, List[ReviewComment]] = {}
        # review_id -> List[ReviewAuditLog]
        self._audit_logs: Dict[str, List[ReviewAuditLog]] = {}

    # ------------------------------------------------------------------
    # 状态管理
    # ------------------------------------------------------------------
    def get_status(self, review_id: str) -> Optional[str]:
        """获取当前状态."""
        return self._statuses.get(review_id)

    def init_status(self, review_id: str, initial: str = ReviewWorkflowStatus.COMPLETED.value) -> str:
        """初始化审核记录状态（审核完成后自动调用）."""
        if review_id not in self._statuses:
            self._statuses[review_id] = initial
            self._add_audit_log(review_id, "init", None, initial, None, "系统自动初始化")
        return self._statuses[review_id]

    def transition(
        self,
        review_id: str,
        action: str,
        operator: Optional[str] = None,
        note: Optional[str] = None,
    ) -> Optional[str]:
        """执行状态流转.

        Returns:
            新的状态，如果流转非法则返回 None
        """
        current = self._statuses.get(review_id)
        if not current:
            return None

        transitions = self._TRANSITIONS.get(current, {})
        target = transitions.get(action)
        if not target:
            return None

        self._statuses[review_id] = target
        self._add_audit_log(review_id, action, current, target, operator, note)
        return target

    def get_allowed_actions(self, review_id: str) -> List[str]:
        """获取当前允许的操作列表."""
        current = self._statuses.get(review_id)
        if not current:
            return []
        transitions = self._TRANSITIONS.get(current, {})
        return list(transitions.keys())

    # ------------------------------------------------------------------
    # 审核意见
    # ------------------------------------------------------------------
    def add_comment(
        self,
        review_id: str,
        author: str,
        content: str,
        author_role: str = "reviewer",
        issue_refs: Optional[List[str]] = None,
        action: Optional[str] = None,
    ) -> ReviewComment:
        """添加审核意见."""
        comment = ReviewComment(
            comment_id=str(uuid.uuid4()),
            review_id=review_id,
            author=author,
            author_role=author_role,
            content=content,
            issue_refs=issue_refs or [],
            action=action,
            created_at=beijing_now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        self._comments.setdefault(review_id, []).append(comment)
        return comment

    def get_comments(self, review_id: str) -> List[ReviewComment]:
        """获取审核意见列表."""
        return list(self._comments.get(review_id, []))

    def delete_comment(self, review_id: str, comment_id: str) -> bool:
        """删除审核意见."""
        comments = self._comments.get(review_id, [])
        for i, c in enumerate(comments):
            if c.comment_id == comment_id:
                comments.pop(i)
                return True
        return False

    # ------------------------------------------------------------------
    # 流程摘要
    # ------------------------------------------------------------------
    def get_summary(self, review_id: str) -> Optional[WorkflowSummary]:
        """获取流程状态摘要."""
        current = self._statuses.get(review_id)
        if not current:
            return None

        allowed = self.get_allowed_actions(review_id)
        comments = self._comments.get(review_id, [])
        logs = self._audit_logs.get(review_id, [])

        return WorkflowSummary(
            review_id=review_id,
            current_status=current,
            status_display=self._STATUS_DISPLAY.get(current, current),
            can_review="review" in allowed,
            can_approve="approve" in allowed,
            can_reject="reject" in allowed,
            can_revise="revise" in allowed,
            can_archive="archive" in allowed,
            comments_count=len(comments),
            audit_logs=logs,
        )

    def get_all_status_counts(self) -> Dict[str, int]:
        """获取各状态数量统计."""
        counts: Dict[str, int] = {}
        for status in self._statuses.values():
            counts[status] = counts.get(status, 0) + 1
        return counts

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------
    def _add_audit_log(
        self,
        review_id: str,
        action: str,
        from_status: Optional[str],
        to_status: Optional[str],
        operator: Optional[str],
        note: Optional[str],
    ):
        """添加审计日志."""
        log = ReviewAuditLog(
            log_id=str(uuid.uuid4()),
            review_id=review_id,
            action=action,
            from_status=from_status,
            to_status=to_status,
            operator=operator,
            note=note,
            created_at=beijing_now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        self._audit_logs.setdefault(review_id, []).append(log)