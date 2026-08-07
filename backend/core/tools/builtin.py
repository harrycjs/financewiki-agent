"""内置工具：read_file / write_file / bash / web_search

沙箱策略（见 settings.allowed_roots）：
- read_file / write_file 的路径经 resolve 后必须落在某个允许根内
- bash 以 WORKSPACE_ROOT 为 cwd，但允许通过绝对路径或相对路径访问 skills 根里的脚本
- 危险命令黑名单 + 超时双保险
"""
import asyncio
from pathlib import Path
import platform
import re
from typing import Any, Dict, List

import httpx

from ...config import settings
from .base import BaseTool, ToolError, resolve_in_allowed_roots, truncate


class ReadFileTool(BaseTool):
    name = "read_file"
    description = (
        "读取允许目录内某个文本文件的内容。可用于：\n"
        "- 查看之前写入的分析结果、数据文件\n"
        "- 按需读取技能目录里的 .md / .py / .txt 文件（references/、scripts/ 等）\n"
        "- 读取配置文件等\n"
        "传目录路径则列出该目录下的条目。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "文件路径（相对当前 cwd 或绝对路径）；传目录路径则列出条目",
            }
        },
        "required": ["path"],
    }

    async def run(self, path: str) -> str:
        target = resolve_in_allowed_roots(path)
        if not target.exists():
            raise ToolError(f"文件不存在：{path}")
        if target.is_dir():
            entries = sorted(p.name + ("/" if p.is_dir() else "") for p in target.iterdir())
            return f"{path} 是一个目录，包含：\n" + "\n".join(entries) if entries else f"{path} 是一个空目录"

        try:
            raw = await asyncio.to_thread(target.read_bytes)
        except Exception as e:
            raise ToolError(f"读取失败：{e}")
        # UTF-8 → GBK → latin-1：Windows 上很多脚本（含 init_skill.py）
        # 默认 encoding 写文件 → GBK，不 fallback agent 就要走 bash 绕路
        content = None
        for enc in ("utf-8", "gbk", "latin-1"):
            try:
                content = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if content is None:
            content = raw.decode("utf-8", errors="replace")
        return truncate(content)


class WriteFileTool(BaseTool):
    name = "write_file"
    description = (
        "在允许目录内写入文本文件（覆盖已有内容）。可用于保存分析报告、"
        "中间数据，或在 skills 目录下创建 / 更新技能文件。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "目标文件路径，可以是相对路径或绝对路径，父目录会自动创建",
            },
            "content": {"type": "string", "description": "要写入的完整文本内容"},
        },
        "required": ["path", "content"],
    }

    async def run(self, path: str, content: str) -> str:
        target = resolve_in_allowed_roots(path)
        if target.is_dir():
            raise ToolError(f"{path} 是一个目录，不能作为文件写入")

        await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(target.write_text, content, encoding="utf-8")
        return f"已写入 {path}（{len(content)} 字符）"


# 黑名单只挡明显的破坏性操作。沙箱的主防线是 cwd 限制与超时，
# 这里是第二道，防止模型手滑把宿主机搞坏。
_DANGEROUS = [
    r"\brm\s+(-\w+\s+)*-\w*[rf]\w*\s+/(?!\S)",   # rm -rf /
    r"\bmkfs(\.\w+)?\b",
    r"\bdd\s+.*\bof=/dev/",
    r":\(\)\s*\{.*\}\s*;?\s*:",                   # fork bomb
    r"\b(shutdown|reboot|halt|poweroff)\b",
    r"\bchmod\s+-R\s+777\s+/(?!\S)",
    r">\s*/dev/[sh]d[a-z]",
    r"\bformat\s+[a-z]:",                          # Windows
    r"\bdel\s+/[fsq]\b.*[a-z]:\\",                 # Windows del /f /s /q c:\
    r"\|\s*(sudo\s+)?(ba)?sh\b",                   # curl ... | sh
]


class BashTool(BaseTool):
    name = "bash"
    description = (
        "执行一条 shell 命令并返回 stdout/stderr/stderr。"
        "可用于运行技能脚本（绝对路径如 "
        "`python <skill_dir>/scripts/init_skill.py <args>`）、"
        "处理数据文件、查看目录结构。默认 cwd 是工作目录。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "要执行的 shell 命令，例如 'ls -la' 或 'python /abs/path/to/skill/scripts/init_skill.py my-skill --path .'",
            }
        },
        "required": ["command"],
    }

    async def run(self, command: str) -> str:
        for pattern in _DANGEROUS:
            if re.search(pattern, command, re.IGNORECASE):
                raise ToolError(f"命令被安全策略拒绝（匹配危险模式）：{command}")

        # 直接用 WORKSPACE_ROOT 而非 resolve_in_allowed_roots(".")
        # 因为后者以 Path.cwd() 为基准，如果进程的 cwd 不在允许根里就报错
        cwd = Path(settings.WORKSPACE_ROOT).resolve()
        cwd.mkdir(parents=True, exist_ok=True)
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(cwd),
            )
        except NotImplementedError:
            # Windows 上如果事件循环不是 Proactor 会走到这里
            raise ToolError("当前事件循环不支持子进程，bash 工具不可用")

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=settings.BASH_TIMEOUT
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise ToolError(f"命令超时（>{settings.BASH_TIMEOUT}s）已被终止：{command}")

        encoding = "gbk" if platform.system() == "Windows" else "utf-8"
        out = stdout.decode(encoding, errors="replace").strip()
        err = stderr.decode(encoding, errors="replace").strip()

        parts = [f"exit code: {proc.returncode}"]
        if out:
            parts.append(f"stdout:\n{out}")
        if err:
            parts.append(f"stderr:\n{err}")
        if not out and not err:
            parts.append("(无输出)")
        return truncate("\n\n".join(parts))


class WebSearchTool(BaseTool):
    name = "web_search"
    description = (
        "联网搜索实时信息。当问题涉及最新行情、新闻、公告、政策，"
        "或知识库文档中没有覆盖的内容时使用。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词，用自然语言描述要查什么"},
            "max_results": {
                "type": "integer",
                "description": "返回结果条数，默认 5，最多 10",
            },
        },
        "required": ["query"],
    }

    async def run(self, query: str, max_results: int = 5) -> str:
        if not settings.TAVILY_API_KEY:
            raise ToolError(
                "联网搜索未配置：请在 .env 里设置 TAVILY_API_KEY"
                "（去 https://tavily.com 免费注册）"
            )

        max_results = max(1, min(int(max_results or 5), 10))
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{settings.TAVILY_API_BASE}/search",
                headers={"Authorization": f"Bearer {settings.TAVILY_API_KEY}"},
                json={
                    "query": query,
                    "max_results": max_results,
                    "search_depth": "basic",
                    "include_answer": True,
                },
                timeout=30,
            )

        if response.status_code != 200:
            raise ToolError(f"搜索接口返回 {response.status_code}: {response.text[:200]}")

        data = response.json()
        return truncate(self._format(data))

    @staticmethod
    def _format(data: Dict[str, Any]) -> str:
        parts: List[str] = []
        if data.get("answer"):
            parts.append(f"【摘要】{data['answer']}")

        for i, item in enumerate(data.get("results") or [], 1):
            title = item.get("title", "无标题")
            url = item.get("url", "")
            content = (item.get("content") or "").strip()
            parts.append(f"[{i}] {title}\n{url}\n{content}")

        return "\n\n".join(parts) if parts else "没有搜到相关结果"
