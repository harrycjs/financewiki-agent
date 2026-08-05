"""
Skills 管理接口

设计：技能 = backend/skills/<name>/SKILL.md 文件
所有 CRUD 都是文件 IO，不进数据库。
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from ..core.skills.scanner import get_scanner

router = APIRouter(prefix="/api/skills", tags=["skills"])


# ---------- Pydantic 模型 ----------

class SkillCreate(BaseModel):
    """创建技能"""
    name: str
    content: str  # 完整 SKILL.md（含 frontmatter + 正文）


class SkillUpdate(BaseModel):
    """更新技能内容"""
    content: str


class SkillTestRequest(BaseModel):
    """测试技能：输入示例问题，返回 system_prompt 拼接预览（不调 LLM）"""
    query: str


# ---------- 端点 ----------

@router.get("")
async def list_skills():
    """列出所有技能（仅元数据，不含正文）"""
    skills = get_scanner().list_skills()
    return [
        {
            "name": s.get("name"),
            "title": s.get("title"),
            "description": s.get("description"),
            "category": s.get("category", "general"),
            "trigger_keywords": s.get("trigger_keywords") or [],
            "enabled": bool(s.get("enabled", True)),
            "is_preset": bool(s.get("is_preset", False)),
        }
        for s in skills
    ]


@router.get("/{name}")
async def get_skill(name: str):
    """取单个技能详情（含正文）"""
    skill = get_scanner().get_skill(name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"技能 '{name}' 不存在")
    return {
        "name": skill.get("name"),
        "title": skill.get("title"),
        "description": skill.get("description"),
        "category": skill.get("category", "general"),
        "trigger_keywords": skill.get("trigger_keywords") or [],
        "enabled": bool(skill.get("enabled", True)),
        "is_preset": bool(skill.get("is_preset", False)),
        "body": skill.get("body", ""),
        "raw": skill.get("raw", ""),
    }


@router.post("")
async def create_skill(payload: SkillCreate):
    """创建新技能（生成文件夹 + SKILL.md）"""
    try:
        skill = get_scanner().create_skill(payload.name, payload.content)
    except FileExistsError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"message": "技能创建成功", "name": skill.get("name")}


@router.put("/{name}")
async def update_skill(name: str, payload: SkillUpdate):
    """更新现有技能内容"""
    try:
        skill = get_scanner().update_skill(name, payload.content)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"message": "技能更新成功", "name": skill.get("name")}


@router.delete("/{name}")
async def delete_skill(name: str):
    """删除技能文件夹。预置技能拒绝。"""
    try:
        deleted = get_scanner().delete_skill(name)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    if not deleted:
        raise HTTPException(status_code=404, detail=f"技能 '{name}' 不存在")
    return {"message": "技能已删除", "name": name}


@router.post("/{name}/toggle")
async def toggle_skill(name: str):
    """切换 enabled 字段"""
    try:
        skill = get_scanner().toggle_skill(name)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {
        "message": "已切换",
        "name": name,
        "enabled": bool(skill.get("enabled", True)),
    }


@router.post("/{name}/test")
async def test_skill(name: str, payload: SkillTestRequest):
    """测试技能：返回 system_prompt 拼接预览（不实际调 LLM）

    用法：选中一个技能 + 输入示例问题 → 后端构造完整 system_prompt
    → 返回给前端展示，让用户直观看到技能被注入 prompt 后的样子。
    """
    skill = get_scanner().get_skill(name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"技能 '{name}' 不存在")

    # 复用 generator._build_system_prompt 的逻辑
    from ..core.rag.skill_resolver import get_resolver
    from ..core.rag.generator import BASE_SYSTEM_PROMPT

    resolver = get_resolver()

    # 假设启用当前测试的技能（即使它被禁用）
    index_text = resolver.get_active_index()
    selected = resolver.select_relevant_skills(payload.query)
    full_text = resolver.get_full_instructions(selected)

    skills_block = ""
    if index_text:
        skills_block += f"\n\n## 可用技能索引\n{index_text}\n\n（当你判断需要使用某个技能时，请遵循其完整指令）"
    if full_text:
        skills_block += f"\n\n## 当前已加载技能\n{full_text}"

    system_prompt = BASE_SYSTEM_PROMPT + skills_block

    return {
        "skill_name": name,
        "skill_title": skill.get("title"),
        "query": payload.query,
        "selected_skills": selected,
        "system_prompt": system_prompt,
        "system_prompt_length": len(system_prompt),
        "index_length": len(index_text),
        "full_instructions_length": len(full_text),
    }


@router.post("/reload")
async def reload_skills():
    """手动重新扫描 skills/ 目录（一般不需要，启动已自动扫）"""
    scanner = get_scanner()
    scanner.invalidate_cache()
    skills = scanner.list_skills()
    return {
        "message": "已重新扫描",
        "count": len(skills),
    }