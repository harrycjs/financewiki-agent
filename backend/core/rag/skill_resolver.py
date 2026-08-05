"""
Skills 解析器 —— 渐进式披露的核心

三阶段注入：
1. get_active_index() —— 始终注入"轻量索引"（name+description，< 1k token）
2. select_relevant_skills(query) —— 关键词预筛，挑出 1-3 个相关技能
3. get_full_instructions(names) —— 按需注入完整正文

设计动机：避免 system prompt 被所有技能的完整 instructions 撑爆。
"""

from __future__ import annotations

from typing import Dict, List, Optional

import jieba

from ..skills.scanner import SkillScanner, get_scanner


class SkillResolver:
    """技能解析器：扫描器之上的语义层"""

    def __init__(self, scanner: Optional[SkillScanner] = None):
        self.scanner = scanner or get_scanner()

    # ---------- 阶段 1: 索引（轻量，始终注入） ----------

    def get_active_index(self) -> str:
        """所有 enabled=true 技能的格式化索引，注入 system prompt

        格式示例：
            - [financial-ratio-analysis] (analysis): 财务三表比率计算...
            - [valuation-summary] (analysis): 公司估值方法汇总...

        无任何启用技能时返回空字符串
        """
        skills = [s for s in self.scanner.list_skills() if s.get("enabled", True)]
        if not skills:
            return ""

        lines = []
        for s in skills:
            name = s.get("name", "?")
            category = s.get("category", "general")
            desc = s.get("description", "").strip() or "(无描述)"
            lines.append(f"- [{name}] ({category}): {desc}")

        return "\n".join(lines)

    # ---------- 阶段 2: 关键词预筛 ----------

    def select_relevant_skills(self, query: str, top_k: int = 3) -> List[str]:
        """对 query 做关键词预筛，返回相关技能 name 列表

        评分规则（简化版 jieba + 子串匹配）：
        - 每个触发关键词命中 +3
        - 技能 name 子串命中 +2
        - 技能 description 子串命中 +1
        分数 >= 2 才算"相关"，最多返回 top_k
        """
        if not query:
            return []

        skills = [s for s in self.scanner.list_skills() if s.get("enabled", True)]
        if not skills:
            return []

        # 用 jieba 分词得到 query tokens
        query_tokens = set(t.strip() for t in jieba.cut(query) if t.strip())
        query_lower = query.lower()

        scored = []
        for s in skills:
            score = 0
            matched_keywords = []

            # 触发关键词命中
            triggers = s.get("trigger_keywords") or []
            if isinstance(triggers, list):
                for kw in triggers:
                    kw_str = str(kw).strip()
                    if not kw_str:
                        continue
                    if kw_str in query or kw_str.lower() in query_lower:
                        score += 3
                        matched_keywords.append(kw_str)
                    elif any(t in kw_str for t in query_tokens if len(t) > 1):
                        score += 1

            # name 子串
            name = str(s.get("name", ""))
            if name and (name in query_lower or any(t in name for t in query_tokens if len(t) > 1)):
                score += 2

            # description 子串
            desc = str(s.get("description", ""))
            if desc:
                for t in query_tokens:
                    if len(t) > 1 and t in desc:
                        score += 1
                        break

            if score >= 2:
                scored.append((score, name, matched_keywords))

        scored.sort(key=lambda x: (-x[0], x[1]))
        return [name for _, name, _ in scored[:top_k]]

    # ---------- 阶段 3: 完整 instructions ----------

    def get_full_instructions(self, names: List[str]) -> str:
        """按 name 列表取完整 SKILL.md 正文，拼接返回

        每个技能格式：
            ### 技能名 (title)
            body

        无 names 时返回空字符串
        """
        if not names:
            return ""

        blocks = []
        for name in names:
            skill = self.scanner.get_skill(name)
            if not skill:
                continue
            if not skill.get("enabled", True):
                continue
            title = skill.get("title", name)
            body = skill.get("body", "").strip()
            if not body:
                continue
            blocks.append(f"### {title} (`{name}`)\n\n{body}")

        return "\n\n---\n\n".join(blocks)

    # ---------- 辅助：缓存 key ----------

    def get_active_skills_signature(self) -> str:
        """生成稳定签名：已启用技能 name 的有序拼接

        用于让 query cache 在技能切换时自动失效
        """
        names = sorted(
            s.get("name", "")
            for s in self.scanner.list_skills()
            if s.get("enabled", True)
        )
        return ",".join(names)


_default_resolver: Optional[SkillResolver] = None


def get_resolver() -> SkillResolver:
    global _default_resolver
    if _default_resolver is None:
        _default_resolver = SkillResolver()
    return _default_resolver