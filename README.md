# FinanceWiki Agent · 金融投研知识库问答系统

> 一个轻量级金融投研 RAG Agent，基于三路召回检索（向量语义 + BM25 关键词 + 知识图谱），支持多轮对话记忆、多模型切换，以及文件型技能系统（Claude Code Skills 风格）。

![架构](docs/architecture.png)

---

## ✨ 核心特性

### 🎯 三路召回检索（Triple Retrieval）
- **向量语义检索**：Qdrant + 本地 BGE 中文 embedding
- **关键词检索**：jieba + BM25
- **知识图谱检索**：实体识别 + 关系抽取 + 子图扩展
- **RRF 融合排序**：三路结果加权融合 + 重排序

### 🧠 三层记忆系统
- **短期记忆**：Redis 缓存当前会话上下文（30 分钟 TTL）
- **中期记忆**：SQLite 存储相似历史问答
- **长期记忆**：用户画像与核心关注点统计

### 📚 技能系统（Skills，渐进式披露）
- 每个技能 = `backend/skills/<name>/SKILL.md` 一个文件夹
- YAML frontmatter（`name/title/description/category/trigger_keywords/enabled`）+ Markdown 正文
- 三阶段注入到 system prompt：
  1. 始终注入"技能索引"（轻量，< 1k token）
  2. 关键词预筛挑出相关技能（jieba + 子串匹配）
  3. 按需加载选中技能的完整 instructions
- 预置 3 个示例技能：财务比率分析、估值汇总、研报摘要

### 📊 知识图谱
- 上传文档自动抽取实体（公司/指标/人物/行业/概念）
- 跨文档同名实体自动合并为单一节点
- vis-network 力导向图可视化

### 🤖 多模型支持
智谱 GLM-4 · DeepSeek · Kimi · MiniMax

### 💬 流式对话
- SSE 流式输出
- 会话历史持久化（SQLite）
- 自动重连机制

---

## 🛠️ 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3.10+, FastAPI, Uvicorn |
| 前端 | Vue 3, Element Plus, Pinia, vis-network |
| 向量库 | Qdrant |
| 缓存/队列 | Redis |
| 元数据存储 | SQLite |
| 知识图谱 | NetworkX |
| Embedding | BAAI/bge-large-zh-v1.5（本地）|
| 分词 | jieba |

---

## 🚀 快速开始

### 1. 环境要求

- Python 3.10+
- Redis 7+
- Qdrant 1.7+
- Node.js 16+（前端开发时需要）

### 2. 启动依赖服务（Windows 一键脚本）

双击 `start_all.bat`，脚本会：
- 启动 Redis（端口 6379）
- 启动 Qdrant（HTTP 6333 / gRPC 6334）
- 等待端口探活后启动后端（端口 8000）

也可手动用 Docker：
```bash
docker-compose up -d
```

### 3. 安装 Python 依赖

```bash
pip install -r requirements.txt
```

### 4. 配置 API Key

复制 `.env.example` 为 `.env`，填入大模型 API Key：

```env
DEEPSEEK_API_KEY=sk-xxx
ZHIPU_API_KEY=
KIMI_API_KEY=
MINIMAX_API_KEY=
```

### 5. 启动后端

```bash
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

首次启动会自动下载 embedding 模型到 `D:/models/`。

### 6. 构建前端

```bash
cd frontend
npm install
npm run build
cd ..
```

构建产物 `frontend/dist/` 会被后端 `StaticFiles` 自动挂载。

### 7. 访问

- 🌐 应用：http://localhost:8000
- 📖 API 文档：http://localhost:8000/docs
- 🧩 技能管理：http://localhost:8000/skills

---

## 📁 项目结构

```
finance-agent/
├── backend/
│   ├── main.py                    # FastAPI 入口 + 路由注册 + SPA 挂载
│   ├── config.py                  # pydantic-settings 配置
│   ├── database.py                # SQLite 初始化
│   ├── api/                       # REST 路由
│   │   ├── chat.py                # 对话（流式 SSE）+ 会话管理
│   │   ├── documents.py           # 文档上传/解析/重处理
│   │   ├── models.py              # 模型配置 + 切换 + 测试
│   │   ├── knowledge_graph.py     # KG 聚合视图
│   │   └── skills.py              # 技能 CRUD（文件 IO）
│   ├── core/
│   │   ├── rag/
│   │   │   ├── retriever.py       # 三路召回 + RRF 融合
│   │   │   ├── vector_store.py    # Qdrant 封装
│   │   │   ├── generator.py       # 响应生成（注入技能到 system prompt）
│   │   │   └── skill_resolver.py  # 技能渐进式披露解析器
│   │   ├── skills/
│   │   │   └── scanner.py         # 文件扫描 + YAML frontmatter 解析
│   │   ├── knowledge_graph/       # KG 构建与检索
│   │   ├── memory/                # 三层记忆
│   │   ├── cache/                 # 缓存（Redis/内存）
│   │   ├── llm/                   # 大模型基类 + 4 个 provider 实现
│   │   └── embedding/             # BGE 本地 embedding
│   ├── services/                  # 业务服务（文档解析）
│   ├── queue/                     # Redis 队列 producer/worker
│   └── skills/                    # ★ 技能文件夹（每技能一个目录 + SKILL.md）
│       ├── financial-ratio-analysis/SKILL.md
│       ├── valuation-summary/SKILL.md
│       └── research-report-summary/SKILL.md
├── frontend/
│   ├── src/
│   │   ├── views/                 # 页面：对话/文档/知识图谱/技能/设置
│   │   ├── stores/                # Pinia
│   │   ├── router/                # Vue Router
│   │   └── App.vue                # 侧边栏菜单
│   └── package.json
├── data/                          # SQLite DB + 文档（git 忽略）
├── logs/                          # 运行日志（git 忽略）
├── start_all.bat                  # Windows 一键启动脚本
├── docker-compose.yml             # Qdrant + Redis
├── requirements.txt
└── README.md
```

---

## 🧩 技能系统使用说明

技能 = 一个文件夹 + 一个 `SKILL.md`：

```
backend/skills/
└── my-skill/
    └── SKILL.md
```

`SKILL.md` 格式：
```markdown
---
name: my-skill
title: 我的技能
description: 一句话说明这个技能做什么（AI 据此判断是否启用）
category: general         # analysis / writing / retrieval / general
trigger_keywords: [关键词1, 关键词2]
enabled: true
---

# 技能正文（Markdown）

## 适用场景
...

## 分析要点
...
```

**添加新技能有 3 种方式**：

1. **UI 方式**：访问 http://localhost:8000/skills，点"新建技能"
2. **API 方式**：`POST /api/skills`，body 是 `{name, content}`
3. **文件方式**：直接在 `backend/skills/` 下创建文件夹，刷新页面或调 `POST /api/skills/reload`

**预置 3 个技能**（不可删除）：财务比率分析、估值汇总、研报摘要。

---

## 🔌 API 接口速览

| 模块 | 端点 |
|---|---|
| 对话 | `POST /api/chat`（流式）, `GET /api/chat/history`, `GET/POST/DELETE /api/chat/sessions`, `POST /api/chat/sessions/recover-orphans` |
| 文档 | `POST /api/documents/upload`, `GET /api/documents`, `DELETE /api/documents/{id}`, `POST /api/documents/{id}/reprocess` |
| 知识图谱 | `GET /api/knowledge-graph`（全局合并视图）, `GET /api/knowledge-graph/stats`, `GET /api/knowledge-graph/entity/{name}` |
| 模型 | `GET /api/models`, `POST /api/models/switch`, `POST /api/models/config/{provider}`, `POST /api/models/test/{provider}` |
| 技能 | `GET /api/skills`, `GET/POST/PUT/DELETE /api/skills/{name}`, `POST /api/skills/{name}/toggle`, `POST /api/skills/{name}/test`, `POST /api/skills/reload` |

完整 OpenAPI 文档：http://localhost:8000/docs

---

## ⚙️ 配置说明

### 环境变量（`.env`）

```env
# Embedding
EMBEDDING_PROVIDER=local      # local / zhipu / deepseek
EMBEDDING_MODEL=BAAI/bge-large-zh-v1.5
EMBEDDING_DIMENSION=1024

# Redis / Qdrant
REDIS_HOST=localhost
REDIS_PORT=6379
QDRANT_HOST=localhost
QDRANT_PORT=6333

# 检索
RETRIEVAL_TOP_K=10
CACHE_TTL=3600
```

### Embedding 模型

默认本地 BGE-large-zh-v1.5（1024 维），首次运行自动下载到 `D:/models/`。
也可改为 API：`EMBEDDING_PROVIDER=zhipu/deepseek`。

---

## 🧠 设计要点

- **渐进式披露**：技能按需注入，token 占用与技能数量解耦
- **真正的增量**：DB 中每个文档独立存储实体/关系，API 层按 (name, type) 合并为全局图
- **流式优先**：对话用 SSE 流式输出，前端 fetch + ReadableStream 解析
- **离线友好**：Redis 不可用时自动降级到内存模式；Qdrant 不可用时同样
- **路由顺序保证**：`/api/*` 路由先于静态文件 catch-all 注册，避免 SPA 抢匹配

---

## 📄 License

MIT