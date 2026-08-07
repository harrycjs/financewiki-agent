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
from pathlib import Path

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
            - [financial-ratio-analysis] (analysis) dir=D:\...\financial-ratio-analysis: 财务三表比率计算...
            - [valuation-summary] (analysis) dir=D:\...\valuation-summary: 公司估值方法汇总...
            - [skill-creator] (general) dir=D:\...\skill-creator: Guide for creating...

        每个技能都暴露整个技能目录的绝对路径（dir=...），让模型知道
        这个技能在硬盘上的根在哪。目录里的任何 .md / .py / .txt 文件
        模型都可以用 read_file 按需读取（不预设具体文件）。

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

            # 暴露整目录：模型按需读其中任意文件
            skill_dir = Path(s.get("path", "")).parent
            lines.append(f"- [{name}] ({category}) dir={skill_dir}: {desc}")

        return "\n".join(lines)

    @staticmethod
    def _list_skill_files(skill_dir: Path) -> List[str]:
        """列出技能目录里所有可读文件（相对路径）

        跳过 __pycache__、点开头的隐藏文件、二进制扩展名（粗略）。
        用于在「完整指令」块尾部告诉模型"还有这些文件可读"。
        """
        if not skill_dir or not skill_dir.is_dir():
            return []

        binary_ext = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".pyc", ".ico", ".woff", ".woff2", ".ttf", ".otf", ".pptx", ".docx", ".xlsx"}
        files: List[str] = []
        for p in skill_dir.rglob("*"):
            if not p.is_file():
                continue
            if any(part.startswith(".") for part in p.relative_to(skill_dir).parts):
                continue
            if "__pycache__" in p.relative_to(skill_dir).parts:
                continue
            if p.suffix.lower() in binary_ext:
                continue
            files.append(str(p.relative_to(skill_dir)).replace("\\", "/"))
        # SKILL.md 已经在正文里读过了，不重复
        files = [f for f in files if f != "SKILL.md"]
        files.sort()
        return files

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

            📁 技能目录：<skill_dir>
            📄 可按需读取的文件：
              - scripts/init_skill.py
              - references/workflows.md
              - ...

        尾部追加目录路径 + 文件清单，让模型在 SKILL.md 正文引用
        "See references/xxx.md" 时知道可以 read_file 取回。

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

            skill_dir = Path(skill.get("path", "")).parent
            files = self._list_skill_files(skill_dir)
            files_block = ""
            if files:
                file_lines = "\n".join(f"  - `{f}`" for f in files)
                files_block = (
                    f"\n\n📁 技能目录：`{skill_dir}`\n"
                    f"📄 目录内可按需用 `read_file` 读取的文件（SKILL.md 已在上方正文）：\n"
                    f"{file_lines}\n"
                    f"（如 SKILL.md 正文里提到 `See references/xxx.md`，请直接 read_file 该路径，不要凭空猜测内容）"
                )

            blocks.append(f"### {title} (`{name}`)\n\n{body}{files_block}")

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