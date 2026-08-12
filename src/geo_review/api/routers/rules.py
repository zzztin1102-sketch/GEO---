"""Rules and industry knowledge base routes."""

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request

from geo_review.auth.schemas import UserResponse
from geo_review.rules.loader import RuleLoader

from .deps import get_current_user

router = APIRouter()


# ================================================================
# 规则模板查询
# ================================================================

@router.get("/api/v1/rules/templates", tags=["规则"])
async def list_rule_templates():
    """获取可用的规则模板列表."""
    templates = RuleLoader.list_templates()
    return {"templates": templates}


@router.get("/api/v1/rules/templates/{template_name}", tags=["规则"])
async def get_rule_template(template_name: str):
    """获取指定规则模板的内容."""
    try:
        rule_set = RuleLoader.from_template(template_name)
        return {"template": template_name, "rules": rule_set.model_dump()}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"模板 '{template_name}' 不存在")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ================================================================
# 行业知识库 API
# ================================================================

@router.get("/api/v1/industry/kb", tags=["行业知识库"])
async def list_industry_kbs(request: Request):
    """获取已加载的行业知识库列表."""
    kbs = request.app.state._industry_kbs
    return {
        "industries": [
            {
                "industry": kb.industry,
                "name": kb.name,
                "version": kb.version,
                "description": kb.description,
                "rules_count": len(kb.compliance_rules),
                "risks_count": len(kb.risk_patterns),
                "terms_count": len(kb.terms),
            }
            for kb in kbs.values()
        ]
    }


@router.get("/api/v1/industry/kb/{industry}", tags=["行业知识库"])
async def get_industry_kb(industry: str, request: Request):
    """获取指定行业知识库的完整内容."""
    kb = request.app.state._industry_kbs.get(industry)
    if not kb:
        raise HTTPException(status_code=404, detail=f"行业知识库 '{industry}' 不存在")
    return kb.model_dump()


@router.get("/api/v1/industry/kb/{industry}/context", tags=["行业知识库"])
async def get_industry_kb_context(industry: str, request: Request):
    """获取指定行业知识库的 LLM 上下文文本."""
    kb = request.app.state._industry_kbs.get(industry)
    if not kb:
        raise HTTPException(status_code=404, detail=f"行业知识库 '{industry}' 不存在")
    return {
        "industry": industry,
        "name": kb.name,
        "llm_context": kb.build_llm_context(),
    }


# ================================================================
# 规则管理 API（动态 CRUD）
# ================================================================

@router.get("/api/v1/rules", tags=["规则"])
async def get_current_rules(
    request: Request,
    current_user: UserResponse = Depends(get_current_user),
):
    """获取当前加载的规则集详情（扁平列表）."""
    rule_set = request.app.state._rule_set
    return {
        "meta": rule_set.meta.model_dump(mode="json"),
        "rules_flat": rule_set.get_all_rules_flat(),
    }


@router.post("/api/v1/rules/validate", tags=["规则"])
async def validate_rule(
    rule_data: Dict[str, Any],
    current_user: UserResponse = Depends(get_current_user),
):
    """验证规则格式是否正确."""
    from geo_review.rules.models import RuleSet
    try:
        test_set = RuleSet(rules={rule_data.get("type", "forbidden_claims"): [rule_data]})
        return {"valid": True, "rule": rule_data}
    except Exception as e:
        return {"valid": False, "error": str(e), "rule": rule_data}


@router.post("/api/v1/rules/test", tags=["规则"])
async def test_rule(
    content: str,
    rule_data: Dict[str, Any],
    current_user: UserResponse = Depends(get_current_user),
):
    """测试规则在正文中的匹配效果."""
    from geo_review.rules.engine import RuleEngine
    from geo_review.rules.models import RuleSet

    try:
        rule_type = rule_data.get("type", "forbidden_claims")
        test_set = RuleSet(rules={rule_type: [rule_data]})
        engine = RuleEngine(test_set)
        issues = engine.check(content)
        return {
            "matched": len(issues) > 0,
            "match_count": len(issues),
            "issues": [i.model_dump(mode="json") for i in issues],
            "execution_logs": [log.model_dump(mode="json") for log in engine.execution_logs],
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"规则测试失败: {str(e)}")


@router.patch("/api/v1/rules/{rule_id}", tags=["规则"])
async def update_rule(
    rule_id: str,
    updates: Dict[str, Any],
    request: Request,
    current_user: UserResponse = Depends(get_current_user),
):
    """更新单条规则（enabled/weight/severity 等）."""
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    rule_set = request.app.state._rule_set
    success = rule_set.update_rule(rule_id, updates)
    if not success:
        raise HTTPException(status_code=404, detail=f"规则 '{rule_id}' 不存在")
    return {"status": "updated", "rule_id": rule_id}


@router.delete("/api/v1/rules/{rule_id}", tags=["规则"])
async def delete_rule(
    rule_id: str,
    request: Request,
    current_user: UserResponse = Depends(get_current_user),
):
    """删除单条规则."""
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    rule_set = request.app.state._rule_set
    success = rule_set.delete_rule(rule_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"规则 '{rule_id}' 不存在")
    return {"status": "deleted", "rule_id": rule_id}


@router.post("/api/v1/rules/add", tags=["规则"])
async def add_rule(
    rule_type: str,
    rule_data: Dict[str, Any],
    request: Request,
    current_user: UserResponse = Depends(get_current_user),
):
    """添加新规则."""
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    rule_set = request.app.state._rule_set
    try:
        new_id = rule_set.add_rule(rule_type, rule_data)
        return {"status": "created", "rule_id": new_id}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"添加规则失败: {str(e)}")
