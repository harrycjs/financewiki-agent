"""工具基类与注册表

工具以 OpenAI function-calling 的 schema 描述，同时提供一份紧凑的
文本索引用于注入 system prompt（与 skills 的渐进式披露保持一致的风格）。
"""
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional

from ...config import settings


class ToolError(Exception):
    """工具执行失败。错误信息会回灌给 LLM，让它自己决定重试还是换路。"""


class BaseTool(ABC):
    """所有工具的基类"""

    name: str = ""
    description: str = ""
    parameters: Dict[str, Any] = {"type": "object", "properties": {}}

    @abstractmethod
    async def run(self, **kwargs) -> str:
        """执行工具，返回给 LLM 看的字符串结果"""

    def schema(self) -> Dict[str, Any]:
        """转成 OpenAI tools 格式"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def resolve_in_allowed_roots(path: str) -> Path:
    """把用户给的路径 resolve 到任意一个允许的根目录内，越界直接拒绝。

    允许根目录由 settings.allowed_roots 决定（WORKSPACE_ROOT + SKILLS_ROOT）。
    用 resolve() 而不是字符串前缀比较，能挡住 `../`、符号链接、`./a/../../b`
    这类绕过。root 自身也 resolve，保证两边可比。
    """
    roots = settings.allowed_roots
    # 保证所有根目录存在
    for root in roots:
        root.mkdir(parents=True, exist_ok=True)

    candidate = Path(path)
    # 绝对路径直接 resolve；相对路径相对于当前 cwd
    target = candidate.resolve() if candidate.is_absolute() else (Path.cwd() / candidate).resolve()

    for root in roots:
        if target == root or root in target.parents:
            return target

    roots_str = "、".join(str(r) for r in roots)
    raise ToolError(
        f"路径越界：{path} 不在允许的根目录 {roots_str} 内。"
        f"只能读写这些目录下的文件。"
    )


# 向后兼容别名：旧代码用 resolve_in_workspace()
def resolve_in_workspace(path: str) -> Path:
    return resolve_in_allowed_roots(path)


def truncate(text: str, limit: Optional[int] = None) -> str:
    """截断回灌给 LLM 的内容，防止单次工具结果撑爆上下文"""
    limit = limit or settings.BASH_MAX_OUTPUT
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n... [输出过长，已截断，共 {len(text)} 字符]"


class ToolRegistry:
    """工具注册表：schema 供 API 调用，prompt_block 供 system prompt 注入"""

    def __init__(self, tools: Optional[List[BaseTool]] = None):
        self._tools: Dict[str, BaseTool] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: BaseTool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[BaseTool]:
        return self._tools.get(name)

    def schemas(self) -> List[Dict[str, Any]]:
        return [t.schema() for t in self._tools.values()]

    def prompt_block(self) -> str:
        """注入 system prompt 的工具清单

        原生 tools 参数已经把 schema 传给模型了，这里再给一份人类可读的说明，
        是为了让模型更清楚「什么时候该用」而不只是「有什么可用」。
        """
        if not self._tools:
            return ""
        lines = [f"- `{t.name}`: {t.description}" for t in self._tools.values()]
        roots = settings.allowed_roots
        roots_str = " / ".join(str(r) for r in roots)
        return (
            "\n\n## 可用工具\n"
            + "\n".join(lines)
            + f"\n\n工具使用规则：\n"
            f"- 文件读写允许的根目录：`{roots_str}`（你已加载的技能目录在第二个根下，可读其中任意 .md/.py/.txt）\n"
            f"- bash 默认 cwd 是 `{roots[0]}`，但允许通过绝对路径访问 skills 目录里的脚本\n"
            f"- SKILL.md 正文中如提到 `See references/xxx.md` / `scripts/xxx.py`，请直接 `read_file` 该路径，不要凭空猜测内容\n"
            f"- 需要实时信息（行情、新闻、最新政策）时必须调用 `web_search`，不要凭记忆回答\n"
            f"- 知识库文档已在上下文中给出，不需要用工具再读一遍\n"
            f"- 最多连续调用 {settings.AGENT_MAX_STEPS} 轮工具，请高效规划"
        )

    async def execute(self, name: str, arguments: Dict[str, Any]) -> str:
        """执行工具。任何异常都转成字符串返回，让 LLM 有机会自我修正。"""
        tool = self.get(name)
        if tool is None:
            return f"错误：不存在名为 '{name}' 的工具。可用工具：{', '.join(self._tools)}"
        try:
            return await tool.run(**arguments)
        except ToolError as e:
            return f"错误：{e}"
        except TypeError as e:
            return f"错误：参数不匹配 - {e}"
        except Exception as e:
            return f"错误：{type(e).__name__}: {e}"


_registry: Optional[ToolRegistry] = None


def get_registry() -> ToolRegistry:
    """进程级单例，与 MemoryManager / SkillScanner 保持一致的取用方式"""
    global _registry
    if _registry is None:
        from .builtin import BashTool, ReadFileTool, WebSearchTool, WriteFileTool

        _registry = ToolRegistry([
            ReadFileTool(),
            WriteFileTool(),
            BashTool(),
            WebSearchTool(),
        ])
    return _registry
