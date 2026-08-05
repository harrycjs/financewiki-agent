# FinanceWiki Agent 项目完成总结

## 项目概述

已完整实现一个轻量级、功能齐全的金融投研知识库问答Agent，满足以下核心需求：

### ✅ 已完成功能

#### 1. 三路召回检索
- **向量语义检索**: 基于Qdrant向量数据库，使用BAAI/bge-small-zh-v1.5本地embedding模型
- **关键词检索**: jieba分词 + BM25算法
- **知识图谱检索**: 实体识别 + 关系提取 + 子图扩展
- **RRF融合**: 三路结果融合排序，知识图谱权重更高

#### 2. 智能记忆系统
- **短期记忆**: Redis缓存，30分钟过期，保持对话连贯性
- **中期记忆**: SQLite存储，30天自动清理，历史问答对
- **长期记忆**: 用户画像和核心观点

#### 3. 多模型支持
- 智谱AI (GLM-4)
- DeepSeek (已配置API Key: sk-fd4df3956bb54948a969b7e7b0056997)
- Kimi (Moonshot)
- MiniMax

#### 4. 知识图谱
- 自动提取5种实体类型：公司、指标、人物、行业、概念
- 自动提取实体关系和权重
- NetworkX内存图 + SQLite持久化
- 前端可视化展示

#### 5. 文档管理
- 支持PDF、Word、Markdown、TXT文件上传
- 自动解析文档内容
- 双粒度切分（段落+句子）
- 异步队列处理（embedding、知识图谱提取）

#### 6. 缓存优化
- Redis查询结果缓存（1小时TTL）
- Redis向量缓存（24小时TTL）
- Redis会话上下文缓存（30分钟TTL）
- 目标响应时间 < 200ms

#### 7. 前端界面
- Vue3 + Element Plus
- 对话界面：流式输出、引用标注
- 文档管理：上传、列表、实体查看
- 知识图谱：可视化、搜索、详情
- 设置页面：模型配置、切换、测试

## 技术架构

```
┌─────────────────────────────────────────────────────────┐
│                    Frontend (Vue3)                      │
│  - 对话界面 + 文件管理 + 知识图谱 + 设置                │
├─────────────────────────────────────────────────────────┤
│                    Backend (FastAPI)                     │
│  - API路由 + 业务逻辑 + 文件处理 + RAG引擎              │
├─────────────────────────────────────────────────────────┤
│                    Storage Layer                        │
│  - Qdrant (向量库) + SQLite (元数据) + NetworkX (KG)   │
│  - Redis (缓存/队列/会话)                               │
└─────────────────────────────────────────────────────────┘
```

## 项目结构

```
finance-agent/
├── backend/
│   ├── main.py                    # FastAPI入口
│   ├── config.py                  # 配置管理
│   ├── database.py                # 数据库初始化
│   ├── api/                       # API路由
│   ├── core/                      # 核心功能
│   │   ├── rag/                  # RAG引擎
│   │   ├── knowledge_graph/      # 知识图谱
│   │   ├── memory/               # 记忆系统
│   │   ├── cache/                # 缓存服务
│   │   ├── llm/                  # 大模型集成
│   │   └── embedding/            # Embedding服务
│   ├── services/                  # 业务服务
│   └── queue/                     # 异步队列
├── frontend/
│   ├── src/
│   │   ├── views/                # 页面组件
│   │   ├── stores/               # 状态管理
│   │   └── router/               # 路由配置
│   └── package.json
├── test/                          # 测试文件
├── data/                          # 数据目录
├── docker-compose.yml            # Docker编排
├── requirements.txt
├── .env                           # 环境变量（已配置DeepSeek API Key）
└── README.md
```

## 快速启动

### 方式一：一键启动（推荐）

```bash
# Windows
start.bat

# Linux/Mac
python start.py
```

### 方式二：手动启动

```bash
# 1. 启动Docker服务
docker-compose up -d

# 2. 安装Python依赖
pip install -r requirements.txt

# 3. 启动后端
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload

# 4. 构建前端（可选）
cd frontend && npm install && npm run build
```

### 访问地址

- **应用地址**: http://localhost:8000
- **API文档**: http://localhost:8000/docs

## 测试

```bash
# 运行测试脚本
cd test
python test_api.py
```

## 面试亮点

### 技术深度
1. **三路召回**: 向量 + 关键词 + 知识图谱，全面覆盖
2. **RRF融合**: 多路结果融合算法，展示排序优化能力
3. **知识图谱**: 实体提取、关系构建、子图扩展
4. **记忆系统**: 三层架构 + 自动整合

### 工程能力
1. **架构设计**: 清晰的分层架构，模块化设计
2. **异步处理**: Redis队列异步处理文档
3. **缓存优化**: 多级缓存策略
4. **代码质量**: 完善的类型提示和文档

### 产品思维
1. **用户体验**: 流畅的对话和图谱可视化
2. **实用功能**: 文件导入、知识图谱、历史追溯
3. **细节处理**: 流式输出、错误处理、加载状态

## 配置说明

### DeepSeek API Key

已在 `.env` 文件中配置：
```
DEEPSEEK_API_KEY=sk-fd4df3956bb54948a969b7e7b0056997
DEEPSEEK_API_BASE=https://api.deepseek.com
```

### Embedding模型

默认使用本地模型 `BAAI/bge-small-zh-v1.5`，首次运行会自动下载。

## 下一步优化方向

1. **性能优化**: 向量量化、GPU加速
2. **功能扩展**: 更多文档格式支持、批量处理
3. **用户体验**: 更丰富的可视化、移动端适配
4. **安全加固**: API密钥加密、访问控制
