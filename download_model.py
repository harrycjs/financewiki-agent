"""
下载BAAI/bge-large-zh-v1.5模型到D盘
"""
from sentence_transformers import SentenceTransformer
import os

# 模型名称
model_name = "BAAI/bge-large-zh-v1.5"

# 保存路径
save_path = "D:/models/BAAI--bge-large-zh-v1.5"

print("=" * 60)
print("  下载BAAI/bge-large-zh-v1.5模型")
print("=" * 60)
print()
print(f"模型名称: {model_name}")
print(f"保存路径: {save_path}")
print()

# 创建目录
os.makedirs(os.path.dirname(save_path), exist_ok=True)

# 检查是否已下载
if os.path.exists(save_path):
    print("✅ 模型已存在，跳过下载")
else:
    print("📥 开始下载模型...")
    print("   这可能需要几分钟时间，请耐心等待...")
    print()

    # 下载模型
    model = SentenceTransformer(model_name)

    # 保存模型
    print()
    print("💾 保存模型到本地...")
    model.save(save_path)

    print()
    print("=" * 60)
    print("  ✅ 模型下载完成！")
    print("=" * 60)

# 验证模型
print()
print("🔍 验证模型...")
model = SentenceTransformer(save_path)
dimension = model.get_sentence_embedding_dimension()
print(f"✅ 模型维度: {dimension}")

# 测试embedding
print()
print("🧪 测试embedding...")
test_text = "这是一个测试文本"
embedding = model.encode(test_text, normalize_embeddings=True)
print(f"✅ 测试文本: {test_text}")
print(f"✅ Embedding维度: {len(embedding)}")
print(f"✅ 前5个值: {embedding[:5]}")

print()
print("=" * 60)
print("  模型准备就绪！")
print("=" * 60)
