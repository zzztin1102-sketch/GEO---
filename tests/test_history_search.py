"""审核历史搜索接口测试 — 验证前端下拉框与后端筛选契约."""

import pytest

from geo_review.history.service import HistoryService


class TestHistorySearchFilters:
    """测试 /api/v1/history 的查询参数行为."""

    def test_verdict_enum_values(self):
        """ReviewVerdict 枚举只有 3 个值 — 验证前端不再包含不存在的 failed."""
        from geo_review.result.models import ReviewVerdict
        assert ReviewVerdict.PASS.value == "pass"
        assert ReviewVerdict.REVISE.value == "revise"
        assert ReviewVerdict.REJECT.value == "reject"
        # Confirm no 'failed' value
        values = [v.value for v in ReviewVerdict]
        assert "failed" not in values, "failed is a status, not a verdict"

    def test_status_enum_values(self):
        """ReviewStatus 枚举包含 failed — 这是前端应筛选的字段."""
        from geo_review.result.models import ReviewStatus
        assert ReviewStatus.COMPLETED.value == "completed"
        assert ReviewStatus.PARTIAL.value == "partial"
        assert ReviewStatus.FAILED.value == "failed"

    def test_history_router_accepts_both_filters(self):
        """确认后端 history 路由同时接受 verdict 和 status 参数."""
        from geo_review.api.routers.history import router
        history_route = None
        for route in router.routes:
            if route.path == "/api/v1/history" and "GET" in route.methods:
                history_route = route
                break

        assert history_route is not None, "history list route not found"

        # 提取 query 参数
        param_names = set()
        for dep in history_route.dependant.query_params:
            param_names.add(dep.name)

        assert "verdict" in param_names, "verdict filter missing"
        assert "status" in param_names, "status filter missing — frontend fix needs this"
        assert "company_name" in param_names
        assert "content_title" in param_names
        assert "task_name" in param_names


class TestDropdownFrontendValues:
    """测试前端下拉框显示的搜索参数值在数据库中能匹配到."""

    @pytest.mark.parametrize("verdict_value", ["pass", "revise", "reject"])
    def test_verdict_values_are_valid(self, verdict_value):
        """前端 verdict 下拉框的所有非"全部"选项必须是有效 verdict."""
        from geo_review.result.models import ReviewVerdict
        valid = {v.value for v in ReviewVerdict}
        assert verdict_value in valid, f"{verdict_value} is not a valid ReviewVerdict"

    @pytest.mark.parametrize("status_value", ["completed", "partial", "failed"])
    def test_status_values_are_valid(self, status_value):
        """前端 status 下拉框的所有非"全部"选项必须是有效 status."""
        from geo_review.result.models import ReviewStatus
        valid = {v.value for v in ReviewStatus}
        assert status_value in valid, f"{status_value} is not a valid ReviewStatus"

    def test_failed_is_status_not_verdict(self):
        """关键契约：failed 是 status 值，不是 verdict 值.

        Bug 原因：原前端 verdict 下拉框包含 option value='failed' 失败，
        这是一个永远查不到结果的无效选项。
        修复：前端分离 verdict 和 status 下拉框，failed 只属于 status。
        """
        from geo_review.result.models import ReviewStatus, ReviewVerdict
        assert "failed" in {v.value for v in ReviewStatus}
        assert "failed" not in {v.value for v in ReviewVerdict}

