# FinanceWiki Agent - 快速启动指南

## 前置要求

- Python 3.10+
- 无需Docker

## 一键启动

```bash
# Windows
start_all.bat
```

## 手动启动

### 1. 启动Redis

```bash
cd D:/tools/redis
./redis-server.exe
```

### 2. 启动Qdrant

```bash
cd D:/tools/qdrant
./qdrant.exe
```

### 3. 启动后端服务

```bash
cd D:/finance-agent
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

## 访问服务

- **应用地址**: http://localhost:8000
- **API文档**: http://localhost:8000/docs
- **Qdrant Dashboard**: http://localhost:6333/dashboard

## 测试功能

### 1. 上传文档

```bash
python -c "
import requests
with open('test/sample.txt', 'rb') as f:
    files = {'file': ('sample.txt', f, 'text/plain')}
    response = requests.post('http://localhost:8000/api/documents/upload', files=files)
    print(response.json())
"
```

### 2. 测试对话

```bash
python -c "
import requests
response = requests.post(
    'http://localhost:8000/api/chat',
    json={'message': '贵州茅台的市盈率是多少？'},
    stream=True
)
for line in response.iter_lines():
    if line:
        print(line.decode('utf-8'))
"
```

## 技术栈

- **后端**: FastAPI + Python 3.10
- **向量数据库**: Qdrant
- **缓存**: Redis
- **Embedding**: BAAI/bge-large-zh-v1.5
- **LLM**: DeepSeek v4 Flash
- **前端**: Vue3 + Element Plus

## 功能特性

✅ 三路召回检索（向量 + 关键词 + 知识图谱）
✅ 多轮对话记忆
✅ 文档上传和解析
✅ 知识图谱构建
✅ 多模型支持
✅ 流式输出

## 故障排除

### Qdrant无法启动

确保端口6333未被占用：
```bash
netstat -ano | findstr :6333
```

### Redis无法启动

确保端口6379未被占用：
```bash
netstat -ano | findstr :6379
```

### 后端服务启动失败

检查日志输出，常见问题：
1. 模型未下载：运行 `python download_model.py`
2. 依赖缺失：运行 `pip install -r requirements.txt`
