"""
Skills 文件扫描器

设计：
- 每个技能 = 一个目录 + 一个 SKILL.md
- SKILL.md = YAML frontmatter（--- 之间）+ Markdown 正文
- 启动时扫描一次，常驻 cache；CRUD 操作后失效并重扫

YAML frontmatter 解析手写（无新增依赖），仅支持本项目用到的字段：
  name / title / description / category / trigger_keywords / enabled
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Any

# Skills 根目录 = backend/skills/
# __file__ = backend/core/skills/scanner.py
SKILLS_DIR: Path = Path(__file__).resolve().parent.parent.parent / "skills"

# 预置技能：不可删除（hardcode 以便用户首次启动就能用，且不会被清空）
PRESET_SKILLS = {
    "financial-ratio-analysis",
    "valuation-summary",
    "research-report-summary",
}


# ---------- YAML frontmatter 解析（极简实现）----------

_FRONT_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)


def parse_skill_md(text: str) -> tuple[Dict[str, Any], str]:
    """解析 SKILL.md → (metadata, body)

    若无 frontmatter 则 metadata 为空 dict、body 为整段文本。
    """
    m = _FRONT_RE.match(text)
    if not m:
        return {}, text.strip()

    raw_meta, body = m.group(1), m.group(2)

    meta: Dict[str, Any] = {}
    current_key: Optional[str] = None
    current_list: Optional[List[str]] = None

    for line in raw_meta.split("\n"):
        stripped = line.rstrip()
        if not stripped or stripped.startswith("#"):
            continue

        # 列表项 "- xxx"
        if stripped.startswith("- ") and current_list is not None:
            current_list.append(stripped[2:].strip())
            continue

        # 新键 "key: value" 或 "key:"
        if ":" in stripped and not stripped.startswith(" "):
            key, _, value = stripped.partition(":")
            key = key.strip()
            value = value.strip()
            current_key = key
            current_list = None

            if not value:
                # 可能是后续的行是列表（如 trigger_keywords: \n  - a \n  - b）
                # 或者下一行不是 "- " 而是值。我们两种都支持：先记空，下一行若是 "- " 则建列表
                meta[key] = None
                current_list = None
                continue

            # 解析值
            if value.startswith("[") and value.endswith("]"):
                # 内联列表 [a, b, c]
                items = [x.strip().strip('"').strip("'") for x in value[1:-1].split(",") if x.strip()]
                meta[key] = items
                current_list = None
            elif value.lower() in ("true", "false"):
                meta[key] = (value.lower() == "true")
                current_list = None
            else:
                # 去掉包裹引号
                if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
                    value = value[1:-1]
                meta[key] = value
                current_list = None
        else:
            # 可能是列表续行（key 后接 -）
            if current_key is not None and meta.get(current_key) is None:
                current_list = []
                meta[current_key] = current_list

    return meta, body.strip()


def serialize_skill_md(metadata: Dict[str, Any], body: str) -> str:
    """把 (metadata, body) 序列化为 SKILL.md 文本

    输出格式稳定：键按固定顺序，便于 diff。
    """
    ordered_keys = ["name", "title", "description", "category", "trigger_keywords", "enabled"]
    seen = set()

    lines = ["---"]
    for k in ordered_keys:
        if k in metadata:
            v = metadata[k]
            if isinstance(v, list):
                lines.append(f"{k}: [{', '.join(str(x) for x in v)}]")
            elif isinstance(v, bool):
                lines.append(f"{k}: {'true' if v else 'false'}")
            else:
                lines.append(f"{k}: {v}")
            seen.add(k)

    # 其它键
    for k, v in metadata.items():
        if k in seen:
            continue
        if isinstance(v, list):
            lines.append(f"{k}: [{', '.join(str(x) for x in v)}]")
        elif isinstance(v, bool):
            lines.append(f"{k}: {'true' if v else 'false'}")
        else:
            lines.append(f"{k}: {v}")

    lines.append("---")
    lines.append("")
    lines.append(body)
    return "\n".join(lines).rstrip() + "\n"


# ---------- Scanner ----------

class SkillScanner:
    """扫描 SKILLS_DIR 下所有 */SKILL.md，提供 CRUD"""

    def __init__(self, skills_dir: Optional[Path] = None):
        self.skills_dir = Path(skills_dir) if skills_dir else SKILLS_DIR
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._loaded = False

    def _ensure_loaded(self):
        if not self._loaded:
            self._load_cache()
            self._loaded = True

    def _load_cache(self):
        """扫描目录，缓存每条技能的元数据"""
        self._cache.clear()
        if not self.skills_dir.exists():
            return
        for entry in self.skills_dir.iterdir():
            if not entry.is_dir():
                continue
            skill_md = entry / "SKILL.md"
            if not skill_md.exists():
                continue
            try:
                meta, _ = parse_skill_md(skill_md.read_text(encoding="utf-8"))
                meta.setdefault("name", entry.name)
                meta["path"] = str(skill_md)
                meta["is_preset"] = entry.name in PRESET_SKILLS
                self._cache[entry.name] = meta
            except Exception as e:
                print(f"⚠️ 解析技能失败 {entry.name}: {e}")

    def invalidate_cache(self):
        self._loaded = False

    # ---- CRUD ----

    def list_skills(self) -> List[Dict[str, Any]]:
        """返回所有技能的元数据列表（不含正文）"""
        self._ensure_loaded()
        return list(self._cache.values())

    def get_skill(self, name: str) -> Optional[Dict[str, Any]]:
        """返回单个技能的完整元数据 + 正文"""
        self._ensure_loaded()
        meta = self._cache.get(name)
        if not meta:
            return None
        path = Path(meta["path"])
        if not path.exists():
            return None
        try:
            full_text = path.read_text(encoding="utf-8")
            _, body = parse_skill_md(full_text)
            return {**meta, "body": body, "raw": full_text}
        except Exception:
            return None

    def create_skill(self, name: str, content: str) -> Dict[str, Any]:
        """创建新技能文件夹 + 写入 SKILL.md

        Raises:
            FileExistsError: name 已存在
            ValueError: name 非法或 content 没有合法 frontmatter
        """
        if not _is_valid_skill_name(name):
            raise ValueError(f"非法技能名 '{name}'，仅允许小写字母/数字/连字符/下划线")

        skill_dir = self.skills_dir / name
        if skill_dir.exists():
            raise FileExistsError(f"技能 '{name}' 已存在")

        meta, body = parse_skill_md(content)
        # 强制 name 与文件夹一致
        meta["name"] = name
        meta.setdefault("title", name)
        meta.setdefault("description", "")
        meta.setdefault("category", "general")
        meta.setdefault("enabled", True)
        meta.setdefault("trigger_keywords", [])

        skill_dir.mkdir(parents=False, exist_ok=False)
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text(serialize_skill_md(meta, body), encoding="utf-8")

        self.invalidate_cache()
        return self.get_skill(name) or {"name": name}

    def update_skill(self, name: str, content: str) -> Dict[str, Any]:
        """更新现有技能的 SKILL.md

        注意：name 不能改（要改名 = 删除 + 新建）
        """
        skill_dir = self.skills_dir / name
        if not skill_dir.exists():
            raise FileNotFoundError(f"技能 '{name}' 不存在")

        meta, body = parse_skill_md(content)
        # 强制 name 与文件夹一致（防止用户改了 frontmatter 里的 name 字段）
        meta["name"] = name

        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text(serialize_skill_md(meta, body), encoding="utf-8")

        self.invalidate_cache()
        return self.get_skill(name) or {"name": name}

    def delete_skill(self, name: str) -> bool:
        """删除整个技能文件夹。预置技能拒绝删除。"""
        if name in PRESET_SKILLS:
            raise PermissionError(f"预置技能 '{name}' 不可删除")
        skill_dir = self.skills_dir / name
        if not skill_dir.exists():
            return False
        shutil.rmtree(skill_dir)
        self.invalidate_cache()
        return True

    def toggle_skill(self, name: str) -> Dict[str, Any]:
        """切换 enabled 字段"""
        skill = self.get_skill(name)
        if not skill:
            raise FileNotFoundError(f"技能 '{name}' 不存在")

        new_enabled = not bool(skill.get("enabled", True))

        # 重写 frontmatter 中只改 enabled
        meta, body = parse_skill_md(skill["raw"])
        meta["enabled"] = new_enabled

        skill_md = Path(skill["path"])
        skill_md.write_text(serialize_skill_md(meta, body), encoding="utf-8")

        self.invalidate_cache()
        return self.get_skill(name) or {"name": name, "enabled": new_enabled}


def _is_valid_skill_name(name: str) -> bool:
    """技能名校验：英文小写/数字/连字符/下划线，长度 1-64"""
    if not name or len(name) > 64:
        return False
    return bool(re.match(r"^[a-z0-9_-]+$", name))


# 单例，便于在 resolver/api/reload 中复用
_default_scanner: Optional[SkillScanner] = None


def get_scanner() -> SkillScanner:
    global _default_scanner
    if _default_scanner is None:
        _default_scanner = SkillScanner()
    return _default_scanner