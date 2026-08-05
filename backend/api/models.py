"""
模型管理接口
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any
import json
import uuid

from ..database import execute_query, get_db
from ..config import settings

router = APIRouter(prefix="/api/models", tags=["models"])


class ModelConfigUpdate(BaseModel):
    """模型配置更新"""
    api_key: Optional[str] = None
    api_base: Optional[str] = None
    config: Optional[Dict[str, Any]] = None


class ModelSwitch(BaseModel):
    """模型切换"""
    model_name: str


# 预定义的模型配置
DEFAULT_MODELS = {
    "zhipu": {
        "name": "GLM-4",
        "provider": "zhipu",
        "api_base": settings.ZHIPU_API_BASE,
        "description": "智谱AI GLM-4模型"
    },
    "deepseek": {
        "name": "DeepSeek",
        "provider": "deepseek",
        "api_base": settings.DEEPSEEK_API_BASE,
        "description": "DeepSeek大模型"
    },
    "kimi": {
        "name": "Kimi",
        "provider": "kimi",
        "api_base": settings.KIMI_API_BASE,
        "description": "Moonshot Kimi模型"
    },
    "minimax": {
        "name": "MiniMax",
        "provider": "minimax",
        "api_base": settings.MINIMAX_API_BASE,
        "description": "MiniMax大模型"
    }
}


@router.get("")
async def get_models():
    """获取支持的模型列表"""
    # 从数据库获取配置
    rows = execute_query(
        """SELECT id, name, provider, api_key, api_base, is_active, config
           FROM model_configs"""
    )

    models = []
    configured_providers = set()

    for row in rows:
        model = {
            "id": row[0],
            "name": row[1],
            "provider": row[2],
            "has_api_key": bool(row[3]),
            "api_base": row[4],
            "is_active": bool(row[5]),
            "config": json.loads(row[6]) if row[6] else {}
        }
        models.append(model)
        configured_providers.add(row[2])

    # 添加未配置的默认模型
    for provider, default_config in DEFAULT_MODELS.items():
        if provider not in configured_providers:
            models.append({
                "id": str(uuid.uuid4()),
                "name": default_config["name"],
                "provider": provider,
                "has_api_key": False,
                "api_base": default_config["api_base"],
                "is_active": False,
                "config": {}
            })

    return models


@router.get("/current")
async def get_current_model():
    """获取当前使用的模型"""
    rows = execute_query(
        """SELECT id, name, provider, api_base, config
           FROM model_configs
           WHERE is_active = 1"""
    )

    if rows:
        row = rows[0]
        return {
            "id": row[0],
            "name": row[1],
            "provider": row[2],
            "api_base": row[3],
            "config": json.loads(row[4]) if row[4] else {}
        }

    # 返回默认模型
    return {
        "id": None,
        "name": "DeepSeek",
        "provider": "deepseek",
        "api_base": settings.DEEPSEEK_API_BASE,
        "config": {}
    }


@router.post("/switch")
async def switch_model(request: ModelSwitch):
    """切换当前模型"""
    with get_db() as conn:
        cursor = conn.cursor()

        # 取消当前激活的模型
        cursor.execute("UPDATE model_configs SET is_active = 0 WHERE is_active = 1")

        # 激活指定模型
        cursor.execute(
            "UPDATE model_configs SET is_active = 1 WHERE provider = ?",
            (request.model_name,)
        )

        # 如果不存在则创建
        if cursor.rowcount == 0:
            model_id = str(uuid.uuid4())
            default_config = DEFAULT_MODELS.get(request.model_name, {})

            # 获取环境变量中的API Key
            api_key = None
            if request.model_name == "zhipu":
                api_key = settings.ZHIPU_API_KEY
            elif request.model_name == "deepseek":
                api_key = settings.DEEPSEEK_API_KEY
            elif request.model_name == "kimi":
                api_key = settings.KIMI_API_KEY
            elif request.model_name == "minimax":
                api_key = settings.MINIMAX_API_KEY

            cursor.execute(
                """INSERT INTO model_configs (id, name, provider, api_key, api_base, is_active)
                   VALUES (?, ?, ?, ?, ?, 1)""",
                (model_id, default_config.get("name", request.model_name),
                 request.model_name, api_key, default_config.get("api_base", ""))
            )

        conn.commit()

    return {"message": f"已切换到 {request.model_name}"}


@router.post("/config/{provider}")
async def update_model_config(provider: str, request: ModelConfigUpdate):
    """更新模型配置"""
    with get_db() as conn:
        cursor = conn.cursor()

        # 检查是否存在
        cursor.execute("SELECT id FROM model_configs WHERE provider = ?", (provider,))
        row = cursor.fetchone()

        if row:
            # 更新
            if request.api_key:
                cursor.execute(
                    "UPDATE model_configs SET api_key = ? WHERE provider = ?",
                    (request.api_key, provider)
                )
            if request.api_base:
                cursor.execute(
                    "UPDATE model_configs SET api_base = ? WHERE provider = ?",
                    (request.api_base, provider)
                )
            if request.config:
                cursor.execute(
                    "UPDATE model_configs SET config = ? WHERE provider = ?",
                    (json.dumps(request.config), provider)
                )
        else:
            # 创建
            model_id = str(uuid.uuid4())
            default_config = DEFAULT_MODELS.get(provider, {})
            cursor.execute(
                """INSERT INTO model_configs (id, name, provider, api_key, api_base, config)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (model_id, default_config.get("name", provider), provider,
                 request.api_key, request.api_base,
                 json.dumps(request.config) if request.config else None)
            )

        conn.commit()

    return {"message": f"{provider} 配置已更新"}


@router.post("/test/{provider}")
async def test_model_connection(provider: str):
    """测试模型连接"""
    from ..core.llm.base import BaseLLM

    # 获取配置
    rows = execute_query(
        "SELECT api_key, api_base FROM model_configs WHERE provider = ?",
        (provider,)
    )

    if not rows:
        raise HTTPException(status_code=404, detail="模型未配置")

    api_key = rows[0][0]
    api_base = rows[0][1]

    if not api_key:
        raise HTTPException(status_code=400, detail="API Key未配置")

    try:
        # 创建LLM实例并测试
        llm = BaseLLM.create(provider, api_key, api_base)
        response = await llm.chat([{"role": "user", "content": "你好，请回复OK"}])
        return {"success": True, "message": "连接成功", "response": response[:100]}
    except Exception as e:
        return {"success": False, "message": f"连接失败: {str(e)}"}
