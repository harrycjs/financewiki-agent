# 金融投研知识库问答Agent - 项目规划

## 一、项目概述

### 1.1 项目定位
一个轻量级、功能齐全的金融投研知识库问答Agent，支持文档导入、三路智能检索、多轮对话，适合作为面试展示项目。

### 1.2 核心特性
- **三路召回检索**：向量语义 + 关键词BM25 + 知识图谱
- **智能检索优化**：查询改写、RRF融合、重排序
- **多轮对话记忆**：短/中/长期记忆系统
- **多模型切换**：支持智谱、DeepSeek、Kimi、MiniMax四种国产大模型
- **轻量级部署**：单命令启动，无需复杂配置

---

## 二、技术架构

### 2.1 技术栈
```
┌─────────────────────────────────────────────────────────┐
│                    Frontend (Vue3)                      │
│  - 对话界面 + 文件管理 + 模型配置                        │
├─────────────────────────────────────────────────────────┤
│                    Backend (FastAPI)                     │
│  - API路由 + 业务逻辑 + 文件处理 + RAG引擎              │
├─────────────────────────────────────────────────────────┤
│                    Storage Layer                        │
│  - Qdrant (向量库) + SQLite (元数据) + NetworkX (KG)   │
│  - Redis (缓存/队列/会话)                               │
└─────────────────────────────────────────────────────────┘
```

### 2.2 目录结构
```
finance-agent/
├── backend/
│   ├── main.py                    # FastAPI入口
│   ├── config.py                  # 配置管理
│   ├── api/                       # API路由
│   │   ├── chat.py               # 对话接口
│   │   ├── documents.py          # 文档管理
│   │   └── models.py             # 模型管理
│   ├── core/                      # 核心功能
│   │   ├── rag/                  # RAG引擎
│   │   │   ├── retriever.py      # 三路召回检索器
│   │   │   ├── reranker.py       # 重排序
│   │   │   ├── knowledge_graph.py # 知识图谱构建与检索
│   │   │   ├── vector_store.py   # Qdrant向量存储
│   │   │   └── generator.py      # 生成器
│   │   ├── memory/               # 记忆系统
│   │   │   ├── short_term.py     # 短期记忆 (Redis)
│   │   │   ├── mid_term.py       # 中期记忆 (SQLite)
│   │   │   └── long_term.py      # 长期记忆 (ChromaDB)
│   │   └── llm/                  # 大模型集成
│   │       ├── base.py           # 基类
│   │       ├── zhipu.py          # 智谱
│   │       ├── deepseek.py       # DeepSeek
│   │       ├── kimi.py           # Kimi
│   │       └── minimax.py        # MiniMax
│   ├── services/                  # 业务服务
│   │   ├── document_service.py   # 文档处理
│   │   ├── embedding_service.py  # 嵌入服务
│   │   └── cache_service.py      # 缓存服务 (Redis)
│   ├── queue/                     # 异步队列
│   │   ├── producer.py           # 生产者
│   │   └── worker.py             # 消费者
│   └── utils/                     # 工具函数
├── frontend/
│   ├── src/
│   │   ├── views/                # 页面
│   │   ├── components/           # 组件
│   │   ├── stores/               # 状态管理
│   │   └── api/                  # API调用
│   └── public/
├── data/                          # 数据目录
│   ├── documents/                # 原始文档
│   └── knowledge_graph/          # 知识图谱数据
├── docker-compose.yml            # Docker编排 (Qdrant + Redis)
├── requirements.txt
└── README.md
```

---

## 三、核心功能设计

### 3.1 三路召回检索（检索优化重点）

#### 检索策略
```python
# 三阶段检索架构 (目标: <200ms)
1. 缓存层 (Cache Hit) - Redis
   - 热门查询预计算结果缓存
   - 查询向量缓存，避免重复计算
   - TTL自动过期，LRU淘汰
   
2. 粗检索 (Recall) - 三路并行
   - 向量检索 (Semantic): Qdrant + 国产embedding
   - 关键词检索 (Lexical): jieba分词 + BM25算法
   - 知识图谱检索 (KG): 实体识别 + 关系查询
   
3. 精检索 (Precision)
   - 混合融合: RRF (Reciprocal Rank Fusion) 融合三路结果
   - 相关性过滤: 阈值过滤低质量结果
   
4. 重排序 (Rerank)
   - Cross-Encoder重排序
   - 上下文压缩: 提取关键段落
```

#### 知识图谱构建
```python
class KnowledgeGraphBuilder:
    """从文档中提取实体和关系，构建知识图谱"""
    
    def __init__(self, llm):
        self.llm = llm
        self.graph = nx.DiGraph()  # NetworkX有向图
    
    async def extract_entities(self, text: str):
        """LLM提取实体（公司、指标、人物等）"""
        prompt = f"""从以下金融文本中提取实体，返回JSON格式：
        文本：{text}
        实体类型：公司名、财务指标、人名、行业、概念
        
        输出格式：
        {{
            "entities": [
                {{"name": "贵州茅台", "type": "公司", "attributes": {"行业": "白酒"}}},
                {{"name": "市盈率", "type": "指标", "attributes": {"类别": "估值"}}}
            ]
        }}"""
        return await self.llm.generate_json(prompt)
    
    async def extract_relations(self, text: str, entities):
        """LLM提取实体关系"""
        prompt = f"""基于以下文本和实体，提取实体间的关系：
        文本：{text}
        实体：{entities}
        
        输出格式：
        {{
            "relations": [
                {{"source": "贵州茅台", "target": "市盈率", "relation": "具有指标", "weight": 0.9}}
            ]
        }}"""
        return await self.llm.generate_json(prompt)
    
    def build_graph(self, documents):
        """构建知识图谱"""
        for doc in documents:
            entities = self.extract_entities(doc.content)
            relations = self.extract_relations(doc.content, entities)
            
            # 添加节点和边
            for entity in entities:
                self.graph.add_node(entity['name'], **entity)
            for rel in relations:
                self.graph.add_edge(rel['source'], rel['target'], **rel)
```

#### 知识图谱检索
```python
class KnowledgeGraphRetriever:
    """基于知识图谱的检索"""
    
    def __init__(self, graph):
        self.graph = graph
    
    async def retrieve(self, query: str, top_k: int = 10):
        """三步检索：实体识别 → 子图扩展 → 相关性排序"""
        # 1. 识别查询中的实体
        entities = await self.extract_query_entities(query)
        
        # 2. 扩展相关实体（1-2跳）
        expanded_entities = self.expand_entities(entities, hops=2)
        
        # 3. 获取相关文档片段
        results = []
        for entity in expanded_entities:
            # 获取与该实体相关的所有文档
            related_docs = self.get_entity_documents(entity)
            for doc in related_docs:
                # 计算相关性分数
                score = self.calculate_relevance(query, entity, doc)
                results.append((doc, score, entity))
        
        # 4. 排序并返回top-k
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]
    
    def expand_entities(self, entities, hops=2):
        """实体扩展：获取1-2跳关联实体"""
        expanded = set(entities)
        frontier = set(entities)
        
        for _ in range(hops):
            new_frontier = set()
            for entity in frontier:
                # 获取邻居节点
                neighbors = set(self.graph.neighbors(entity))
                neighbors.update(self.graph.predecessors(entity))
                new_frontier.update(neighbors - expanded)
            expanded.update(new_frontier)
            frontier = new_frontier
        
        return list(expanded)
```

#### 检索优化点
1. **查询改写**: LLM生成多个查询变体，扩大召回
2. **HyDE**: 假设文档嵌入，提升语义匹配
3. **多粒度切分**: 按段落 + 句子双粒度索引
4. **元数据过滤**: 支持按文档类型、时间、关键词过滤
5. **实体消歧**: 同一实体不同表述的归一化

#### 文档切分策略
```python
class DocumentChunker:
    def __init__(self, max_paragraph_len=500, max_sentence_len=200):
        self.max_paragraph_len = max_paragraph_len
        self.max_sentence_len = max_sentence_len
    
    def chunk(self, document: str):
        """段落+句子双粒度切分"""
        chunks = []
        
        # 1. 按段落切分
        paragraphs = document.split('\n\n')
        
        for para in paragraphs:
            if len(para) <= self.max_paragraph_len:
                # 段落足够短，直接作为chunk
                chunks.append({
                    'type': 'paragraph',
                    'content': para,
                    'metadata': {'level': 'paragraph'}
                })
            else:
                # 段落过长，按句子切分
                sentences = self.split_sentences(para)
                for sent in sentences:
                    if len(sent) <= self.max_sentence_len:
                        chunks.append({
                            'type': 'sentence',
                            'content': sent,
                            'metadata': {'level': 'sentence', 'parent': para[:100]}
                        })
                    else:
                        # 句子仍然过长，强制切分
                        chunks.extend(self.force_split(sent))
        
        return chunks
    
    def split_sentences(self, text):
        """中文句子切分"""
        import re
        sentences = re.split(r'([。！？；\n])', text)
        return [s for s in sentences if s.strip()]
```

### 3.2 缓存与队列系统

#### Redis缓存架构
```python
# 缓存层级
1. 查询结果缓存 (Query Cache)
   - Key: hash(query + model + top_k)
   - Value: 检索结果JSON
   - TTL: 1小时
   - 用途: 热门查询毫秒级响应

2. 向量缓存 (Embedding Cache)
   - Key: hash(text)
   - Value: 向量数组
   - TTL: 24小时
   - 用途: 避免重复计算embedding

3. 会话缓存 (Session Cache)
   - Key: session_id
   - Value: 对话上下文
   - TTL: 30分钟
   - 用途: 保持对话连贯性

4. 热门文档缓存 (Hot Document Cache)
   - Key: doc_id
   - Value: 文档元数据+摘要
   - TTL: 1小时
   - 用途: 加速文档访问
```

#### 缓存服务实现
```python
class CacheService:
    def __init__(self, redis_client):
        self.redis = redis_client
        self.default_ttl = 3600  # 1小时
    
    async def get_query_cache(self, query: str, model: str, top_k: int):
        """获取查询结果缓存"""
        cache_key = self._generate_key(query, model, top_k)
        cached = await self.redis.get(cache_key)
        if cached:
            return json.loads(cached)
        return None
    
    async def set_query_cache(self, query: str, model: str, top_k: int, results: list):
        """设置查询结果缓存"""
        cache_key = self._generate_key(query, model, top_k)
        await self.redis.setex(cache_key, self.default_ttl, json.dumps(results))
    
    async def get_embedding_cache(self, text: str):
        """获取向量缓存"""
        cache_key = f"emb:{hash(text)}"
        cached = await self.redis.get(cache_key)
        if cached:
            return np.frombuffer(cached, dtype=np.float32)
        return None
    
    async def set_embedding_cache(self, text: str, embedding: np.ndarray):
        """设置向量缓存"""
        cache_key = f"emb:{hash(text)}"
        await self.redis.setex(cache_key, 86400, embedding.tobytes())  # 24小时
    
    def _generate_key(self, query: str, model: str, top_k: int):
        """生成缓存Key"""
        return f"query:{hash(query)}:{model}:{top_k}"
```

#### 异步队列架构
```python
# 使用Redis List实现异步队列

# 队列类型
1. 文档处理队列 (document_queue)
   - 任务: 文档解析、索引构建、知识图谱提取
   - 优先级: 普通
   
2. 向量计算队列 (embedding_queue)
   - 任务: 批量计算文档embedding
   - 优先级: 高
   
3. 知识图谱队列 (kg_queue)
   - 任务: 实体提取、关系提取、图谱更新
   - 优先级: 普通

# 队列消费者
class QueueWorker:
    def __init__(self, redis_client):
        self.redis = redis_client
        self.queues = {
            'document': 'document_queue',
            'embedding': 'embedding_queue',
            'kg': 'kg_queue'
        }
    
    async def consume(self, queue_name: str):
        """消费队列任务"""
        while True:
            task = await self.redis.blpop(self.queues[queue_name])
            if task:
                await self.process_task(queue_name, json.loads(task[1]))
    
    async def process_task(self, queue_name: str, task: dict):
        """处理任务"""
        if queue_name == 'document':
            await self.process_document(task)
        elif queue_name == 'embedding':
            await self.process_embedding(task)
        elif queue_name == 'kg':
            await self.process_kg(task)
```

### 3.3 记忆系统

#### 三层记忆架构
```python
# 短期记忆 (Short-term)
- 存储: Redis (Session Cache)
- 内容: 当前会话上下文 (最近10轮对话)
- TTL: 30分钟
- 用途: 保持对话连贯性

# 中期记忆 (Mid-term)
- 存储: SQLite
- 内容: 用户偏好、历史问答对、摘要
- 生命周期: 持久化，定期清理(30天)
- 用途: 个性化回答、历史追溯

# 长期记忆 (Long-term)
- 存储: ChromaDB
- 内容: 用户知识图谱、核心观点、长期洞察
- 生命周期: 永久
- 用途: 深度理解用户需求
```

#### 记忆更新策略
1. **自动提取**: 每轮对话后提取关键信息
2. **重要性评估**: LLM判断信息重要性
3. **定期整合**: 中期记忆定期向长期记忆沉淀

### 3.3 多模型集成

#### 统一接口设计
```python
class BaseLLM:
    def chat(self, messages, **kwargs) -> str
    def stream_chat(self, messages, **kwargs) -> Generator
    def generate_json(self, prompt: str) -> dict  # 用于知识图谱提取
    
# 支持的模型
- 智谱 (GLM-4): ChatGLM API
- DeepSeek: OpenAI兼容API
- Kimi (Moonshot): Moonshot API  
- MiniMax: MiniMax API
```

#### 模型切换
- 前端下拉选择
- 后端动态加载
- 支持不同模型的参数配置

---

## 四、API设计

### 4.1 对话接口
```
POST /api/chat
- 发送消息，获取AI回复
- 支持流式输出
- 自动维护对话上下文

GET /api/chat/history
- 获取对话历史

DELETE /api/chat/history
- 清空对话历史
```

### 4.2 文档管理
```
POST /api/documents/upload
- 上传文档 (PDF/Word/MD/TXT)
- 自动解析、索引、构建知识图谱

GET /api/documents
- 获取文档列表

DELETE /api/documents/{id}
- 删除文档及关联数据

GET /api/documents/{id}/entities
- 获取文档提取的实体

GET /api/documents/{id}/relations
- 获取文档提取的关系
```

### 4.3 知识图谱
```
GET /api/knowledge-graph
- 获取知识图谱概览

GET /api/knowledge-graph/entity/{name}
- 获取实体详情及关联

GET /api/knowledge-graph/search
- 搜索实体和关系

GET /api/knowledge-graph/stats
- 获取图谱统计信息
```

### 4.4 模型管理
```
GET /api/models
- 获取支持的模型列表

POST /api/models/switch
- 切换当前模型

GET /api/models/current
- 获取当前使用的模型

POST /api/models/config
- 配置模型参数
```

---

## 五、前端设计

### 5.1 页面结构
```
├── 对话页面 (Chat)
│   - 消息列表
│   - 输入框
│   - 模型选择
│   - 引用展示
│
├── 文档管理 (Documents)
│   - 文档上传
│   - 文档列表
│   - 实体/关系查看
│
├── 知识图谱 (Knowledge Graph)
│   - 图谱可视化
│   - 实体搜索
│   - 关系浏览
│
└── 设置页面 (Settings)
    - 模型配置
    - API密钥管理
```

### 5.2 技术选型
- **Vue3**: Composition API + setup语法
- **UI组件**: Element Plus
- **状态管理**: Pinia
- **路由**: Vue Router
- **Markdown渲染**: marked + highlight.js
- **HTTP请求**: Axios
- **图谱可视化**: vis.js / D3.js

---

## 六、数据库设计

### 6.1 SQLite表结构
```sql
-- 文档表
CREATE TABLE documents (
    id TEXT PRIMARY KEY,
    filename TEXT,
    file_type TEXT,
    file_path TEXT,
    content TEXT,
    created_at TIMESTAMP,
    updated_at TIMESTAMP
);

-- 实体表
CREATE TABLE entities (
    id TEXT PRIMARY KEY,
    name TEXT,
    type TEXT,  -- 公司、指标、人物、行业、概念
    attributes TEXT,  -- JSON
    doc_id TEXT,
    created_at TIMESTAMP,
    FOREIGN KEY (doc_id) REFERENCES documents(id)
);

-- 关系表
CREATE TABLE relations (
    id TEXT PRIMARY KEY,
    source_id TEXT,
    target_id TEXT,
    relation TEXT,
    weight REAL,
    doc_id TEXT,
    created_at TIMESTAMP,
    FOREIGN KEY (source_id) REFERENCES entities(id),
    FOREIGN KEY (target_id) REFERENCES entities(id),
    FOREIGN KEY (doc_id) REFERENCES documents(id)
);

-- 对话历史表
CREATE TABLE chat_history (
    id INTEGER PRIMARY KEY,
    session_id TEXT,
    role TEXT,  -- user/assistant
    content TEXT,
    sources TEXT,  -- JSON数组，引用来源
    created_at TIMESTAMP
);

-- 模型配置表
CREATE TABLE model_configs (
    id TEXT PRIMARY KEY,
    model_name TEXT,
    provider TEXT,
    api_key TEXT,
    api_base TEXT,
    is_active BOOLEAN,
    created_at TIMESTAMP
);
```

### 6.2 知识图谱存储
```python
# 使用NetworkX内存图 + SQLite持久化
class KnowledgeGraphStore:
    def __init__(self):
        self.graph = nx.DiGraph()  # 内存图，快速查询
        self.db = sqlite3.connect('knowledge_graph.db')  # 持久化
    
    def save(self):
        """保存到SQLite"""
        # 保存节点
        for node, attrs in self.graph.nodes(data=True):
            self.db.execute(
                "INSERT OR REPLACE INTO entities VALUES (?, ?, ?, ?, ?, ?)",
                (node, attrs.get('name'), attrs.get('type'), 
                 json.dumps(attrs), attrs.get('doc_id'), datetime.now())
            )
        
        # 保存边
        for src, tgt, attrs in self.graph.edges(data=True):
            self.db.execute(
                "INSERT OR REPLACE INTO relations VALUES (?, ?, ?, ?, ?, ?, ?)",
                (f"{src}_{tgt}", src, tgt, attrs.get('relation'),
                 attrs.get('weight'), attrs.get('doc_id'), datetime.now())
            )
        
        self.db.commit()
    
    def load(self):
        """从SQLite加载到内存"""
        # 加载节点
        for row in self.db.execute("SELECT * FROM entities"):
            self.graph.add_node(row[0], name=row[1], type=row[2], 
                               attributes=json.loads(row[3]))
        
        # 加载边
        for row in self.db.execute("SELECT * FROM relations"):
            self.graph.add_edge(row[1], row[2], relation=row[3], 
                               weight=row[4])
```

---

## 七、实现计划

### Phase 1: 基础框架 (1-2天)
- [ ] 项目结构搭建
- [ ] FastAPI基础配置
- [ ] SQLite数据库初始化
- [ ] 基础API框架

### Phase 2: RAG核心 (2-3天)
- [ ] Qdrant向量数据库集成
- [ ] Redis缓存服务实现
- [ ] 文档解析器 (PDF/Word/MD/TXT)
- [ ] 三路召回检索实现
- [ ] RRF融合算法
- [ ] 重排序优化
- [ ] 缓存策略实现

### Phase 3: 知识图谱 (2-3天)
- [ ] 实体提取Prompt设计
- [ ] 关系提取Prompt设计
- [ ] NetworkX图谱构建
- [ ] 图谱检索实现
- [ ] 图谱持久化

### Phase 4: 记忆系统 (1-2天)
- [ ] 短期记忆实现
- [ ] 中期记忆实现
- [ ] 长期记忆实现
- [ ] 记忆整合逻辑

### Phase 5: 多模型集成 (1-2天)
- [ ] LLM基类设计
- [ ] 四种模型适配器
- [ ] 模型切换逻辑
- [ ] 流式输出支持

### Phase 6: 前端开发 (2-3天)
- [ ] Vue3项目搭建
- [ ] 对话界面
- [ ] 文档管理界面
- [ ] 知识图谱可视化

### Phase 7: 集成测试 (1天)
- [ ] 端到端测试
- [ ] 性能优化
- [ ] 文档完善

---

## 八、关键代码示例

### 8.1 三路召回检索器
```python
class TripleRetriever:
    """三路召回检索器：向量 + 关键词 + 知识图谱"""
    
    def __init__(self, vector_db, bm25_index, kg_retriever, llm):
        self.vector_db = vector_db
        self.bm25 = bm25_index
        self.kg_retriever = kg_retriever
        self.llm = llm
    
    async def retrieve(self, query: str, top_k: int = 10):
        """三路并行检索 + RRF融合"""
        # 1. 查询改写
        query_variants = await self.generate_query_variants(query)
        
        # 2. 三路并行检索
        vector_results = []
        bm25_results = []
        kg_results = []
        
        for variant in query_variants:
            # 向量检索
            vec_res = await self.vector_db.search(variant, top_k)
            vector_results.extend(vec_res)
            
            # 关键词检索
            bm25_res = self.bm25.search(variant, top_k)
            bm25_results.extend(bm25_res)
            
            # 知识图谱检索
            kg_res = await self.kg_retriever.retrieve(variant, top_k)
            kg_results.extend(kg_res)
        
        # 3. 去重
        vector_results = self.deduplicate(vector_results)
        bm25_results = self.deduplicate(bm25_results)
        kg_results = self.deduplicate(kg_results)
        
        # 4. 三路RRF融合
        merged = self.rrf_fusion(vector_results, bm25_results, kg_results)
        
        # 5. 重排序
        reranked = await self.rerank(query, merged[:top_k*2])
        
        return reranked[:top_k]
    
    def rrf_fusion(self, results_a, results_b, results_c, k=60):
        """三路Reciprocal Rank Fusion"""
        scores = {}
        
        # 向量检索分数
        for rank, doc in enumerate(results_a):
            doc_id = doc.id if hasattr(doc, 'id') else doc[0]
            scores[doc_id] = scores.get(doc_id, 0) + 1 / (k + rank)
        
        # 关键词检索分数
        for rank, doc in enumerate(results_b):
            doc_id = doc.id if hasattr(doc, 'id') else doc[0]
            scores[doc_id] = scores.get(doc_id, 0) + 1 / (k + rank)
        
        # 知识图谱分数（权重更高）
        for rank, item in enumerate(results_c):
            doc_id = item[0].id if hasattr(item[0], 'id') else item[0]
            # 知识图谱结果权重 *1.5
            scores[doc_id] = scores.get(doc_id, 0) + 1.5 / (k + rank)
        
        return sorted(scores.items(), key=lambda x: x[1], reverse=True)
```

### 8.2 知识图谱检索器
```python
class KnowledgeGraphRetriever:
    """基于知识图谱的检索"""
    
    def __init__(self, graph, llm):
        self.graph = graph
        self.llm = llm
    
    async def retrieve(self, query: str, top_k: int = 10):
        """实体识别 → 子图扩展 → 相关性排序"""
        # 1. 识别查询中的实体
        entities = await self.extract_query_entities(query)
        
        # 2. 扩展相关实体（1-2跳）
        expanded_entities = self.expand_entities(entities, hops=2)
        
        # 3. 获取相关文档片段
        results = []
        for entity in expanded_entities:
            # 获取与该实体相关的所有文档
            related_docs = self.get_entity_documents(entity)
            for doc in related_docs:
                # 计算相关性分数
                score = self.calculate_relevance(query, entity, doc)
                results.append((doc, score, entity))
        
        # 4. 排序并返回top-k
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]
    
    async def extract_query_entities(self, query: str):
        """从查询中提取实体"""
        prompt = f"""从以下金融查询中提取实体：
        查询：{query}
        
        返回JSON格式：
        {{"entities": ["实体1", "实体2"]}}
        
        实体类型：公司名、财务指标、人名、行业、概念"""
        
        result = await self.llm.generate_json(prompt)
        return result.get('entities', [])
    
    def expand_entities(self, entities, hops=2):
        """实体扩展：获取1-2跳关联实体"""
        expanded = set(entities)
        frontier = set(entities)
        
        for _ in range(hops):
            new_frontier = set()
            for entity in frontier:
                # 获取邻居节点
                if entity in self.graph:
                    neighbors = set(self.graph.neighbors(entity))
                    neighbors.update(self.graph.predecessors(entity))
                    new_frontier.update(neighbors - expanded)
            expanded.update(new_frontier)
            frontier = new_frontier
        
        return list(expanded)
    
    def get_entity_documents(self, entity: str):
        """获取与实体相关的文档"""
        # 从图中获取实体节点信息
        if entity in self.graph:
            node_data = self.graph.nodes[entity]
            doc_id = node_data.get('doc_id')
            if doc_id:
                return [self.get_document_by_id(doc_id)]
        return []
```

### 8.3 记忆系统
```python
class MemoryManager:
    def __init__(self):
        self.short_term = ShortTermMemory()  # Redis
        self.mid_term = MidTermMemory()      # SQLite
        self.long_term = LongTermMemory()    # ChromaDB
    
    async def get_context(self, session_id: str, query: str):
        # 获取短期记忆 (当前会话)
        short_context = await self.short_term.get(session_id)
        
        # 获取中期记忆 (相关历史)
        mid_context = await self.mid_term.search_similar(query)
        
        # 获取长期记忆 (用户画像)
        long_context = await self.long_term.get_user_profile()
        
        return self.merge_context(short_context, mid_context, long_context)
    
    async def save_interaction(self, session_id: str, user_msg: str, ai_msg: str, sources: list):
        # 保存到短期记忆
        await self.short_term.add(session_id, user_msg, ai_msg)
        
        # 提取关键信息保存到中期记忆
        if await self.is_important(user_msg, ai_msg):
            await self.mid_term.save(user_msg, ai_msg, sources)
```

### 8.4 Qdrant向量存储
```python
from qdrant_client import QdrantClient
from qdrant_client.models import VectorParams, Distance, PointStruct

class QdrantVectorStore:
    """Qdrant向量数据库封装"""
    
    def __init__(self, host: str = "localhost", port: int = 6333):
        self.client = QdrantClient(host=host, port=port)
        self.collection_name = "finance_docs"
        self.vector_size = 1024  # 国产embedding维度
    
    async def init_collection(self):
        """初始化向量集合"""
        # 检查集合是否已存在
        collections = self.client.get_collections().collections
        if self.collection_name not in [c.name for c in collections]:
            # 创建集合，使用余弦相似度
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=VectorParams(
                    size=self.vector_size,
                    distance=Distance.COSINE
                )
            )
    
    async def add_document(self, doc_id: str, embedding: list, metadata: dict):
        """添加文档向量"""
        self.client.upsert(
            collection_name=self.collection_name,
            points=[
                PointStruct(
                    id=doc_id,
                    vector=embedding,
                    payload=metadata
                )
            ]
        )
    
    async def search(self, query_embedding: list, top_k: int = 10, 
                     score_threshold: float = 0.5, filters: dict = None):
        """向量检索"""
        from qdrant_client.models import Filter, FieldCondition, MatchValue
        
        # 构建过滤条件
        search_filter = None
        if filters:
            conditions = []
            for key, value in filters.items():
                conditions.append(
                    FieldCondition(key=key, match=MatchValue(value=value))
                )
            search_filter = Filter(must=conditions)
        
        results = self.client.search(
            collection_name=self.collection_name,
            query_vector=query_embedding,
            limit=top_k,
            score_threshold=score_threshold,
            query_filter=search_filter
        )
        
        return [
            {
                'id': hit.id,
                'score': hit.score,
                'metadata': hit.payload
            }
            for hit in results
        ]
    
    async def batch_add(self, documents: list):
        """批量添加文档"""
        points = [
            PointStruct(
                id=doc['id'],
                vector=doc['embedding'],
                payload=doc['metadata']
            )
            for doc in documents
        ]
        self.client.upsert(
            collection_name=self.collection_name,
            points=points
        )
```

### 8.5 缓存服务
```python
import redis.asyncio as redis
import json
import hashlib
import numpy as np

class CacheService:
    """Redis缓存服务"""
    
    def __init__(self, redis_url: str = "redis://localhost:6379"):
        self.redis = redis.from_url(redis_url, decode_responses=False)
        self.default_ttl = 3600  # 1小时
    
    async def get_query_cache(self, query: str, model: str, top_k: int):
        """获取查询结果缓存"""
        cache_key = self._generate_query_key(query, model, top_k)
        cached = await self.redis.get(cache_key)
        if cached:
            return json.loads(cached)
        return None
    
    async def set_query_cache(self, query: str, model: str, top_k: int, results: list):
        """设置查询结果缓存"""
        cache_key = self._generate_query_key(query, model, top_k)
        await self.redis.setex(cache_key, self.default_ttl, json.dumps(results))
    
    async def get_embedding_cache(self, text: str):
        """获取向量缓存"""
        cache_key = f"emb:{hashlib.md5(text.encode()).hexdigest()}"
        cached = await self.redis.get(cache_key)
        if cached:
            return np.frombuffer(cached, dtype=np.float32)
        return None
    
    async def set_embedding_cache(self, text: str, embedding: np.ndarray):
        """设置向量缓存"""
        cache_key = f"emb:{hashlib.md5(text.encode()).hexdigest()}"
        await self.redis.setex(cache_key, 86400, embedding.tobytes())  # 24小时
    
    async def get_session_context(self, session_id: str):
        """获取会话上下文"""
        cache_key = f"session:{session_id}"
        cached = await self.redis.get(cache_key)
        if cached:
            return json.loads(cached)
        return []
    
    async def set_session_context(self, session_id: str, context: list, ttl: int = 1800):
        """设置会话上下文 (30分钟过期)"""
        cache_key = f"session:{session_id}"
        await self.redis.setex(cache_key, ttl, json.dumps(context))
    
    async def invalidate_query_cache(self):
        """清除所有查询缓存"""
        keys = await self.redis.keys("query:*")
        if keys:
            await self.redis.delete(*keys)
    
    def _generate_query_key(self, query: str, model: str, top_k: int):
        """生成查询缓存Key"""
        key_str = f"{query}:{model}:{top_k}"
        return f"query:{hashlib.md5(key_str.encode()).hexdigest()}"
```

### 8.6 异步队列
```python
import redis.asyncio as redis
import json
from typing import Callable

class AsyncQueue:
    """基于Redis的异步队列"""
    
    def __init__(self, redis_url: str = "redis://localhost:6379"):
        self.redis = redis.from_url(redis_url)
        self.queues = {
            'document': 'queue:document',
            'embedding': 'queue:embedding',
            'kg': 'queue:kg'
        }
        self.handlers = {}
    
    def register_handler(self, queue_name: str, handler: Callable):
        """注册队列处理器"""
        self.handlers[queue_name] = handler
    
    async def enqueue(self, queue_name: str, task: dict):
        """入队任务"""
        queue_key = self.queues.get(queue_name)
        if queue_key:
            await self.redis.rpush(queue_key, json.dumps(task))
    
    async def dequeue(self, queue_name: str, timeout: int = 0):
        """出队任务"""
        queue_key = self.queues.get(queue_name)
        if queue_key:
            result = await self.redis.blpop(queue_key, timeout=timeout)
            if result:
                return json.loads(result[1])
        return None
    
    async def start_worker(self, queue_name: str):
        """启动队列消费者"""
        handler = self.handlers.get(queue_name)
        if not handler:
            raise ValueError(f"No handler registered for queue: {queue_name}")
        
        while True:
            task = await self.dequeue(queue_name, timeout=1)
            if task:
                try:
                    await handler(task)
                except Exception as e:
                    print(f"Error processing task: {e}")
                    # 将失败任务重新入队
                    await self.enqueue(queue_name, task)

# 使用示例
queue = AsyncQueue()

# 注册处理器
async def process_document(task: dict):
    """处理文档任务"""
    doc_id = task['doc_id']
    # 执行文档解析、索引构建等
    print(f"Processing document: {doc_id}")

queue.register_handler('document', process_document)

# 生产者：上传文档时入队
await queue.enqueue('document', {'doc_id': 'doc_123', 'action': 'index'})

# 消费者：后台运行
# await queue.start_worker('document')
```

---

## 九、面试亮点

### 9.1 技术深度
1. **三路召回**: 向量 + 关键词 + 知识图谱，体现检索策略的全面性
2. **知识图谱**: 实体提取、关系构建、子图扩展，展示图数据库应用
3. **记忆系统**: 三层架构 + 自动整合，体现状态管理能力
4. **RRF融合**: 多路结果融合算法，展示排序优化能力

### 9.2 工程能力
1. **架构设计**: 清晰的分层架构，模块化设计
2. **代码质量**: 完善的类型提示和文档
3. **可扩展性**: 插件化的模型和检索器

### 9.3 产品思维
1. **用户体验**: 流畅的对话和图谱可视化
2. **实用功能**: 文件导入、知识图谱、历史追溯
3. **细节处理**: 流式输出、错误处理、加载状态
