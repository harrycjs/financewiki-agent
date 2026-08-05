<template>
  <div class="documents-container">
    <!-- 上传区域 -->
    <div class="upload-section">
      <el-upload
        class="upload-area"
        drag
        action="/api/documents/upload"
        :on-success="handleUploadSuccess"
        :on-error="handleUploadError"
        :before-upload="beforeUpload"
        multiple
      >
        <el-icon :size="48"><UploadFilled /></el-icon>
        <div class="el-upload__text">
          拖拽文件到此处，或 <em>点击上传</em>
        </div>
        <template #tip>
          <div class="el-upload__tip">
            支持 PDF、Word、Markdown、TXT 文件
          </div>
        </template>
      </el-upload>
    </div>

    <!-- 文档列表 -->
    <div class="document-list">
      <div class="list-header">
        <h3>文档列表</h3>
        <el-tag type="info">共 {{ documents.length }} 个文档</el-tag>
      </div>

      <el-table :data="documents" style="width: 100%" v-loading="loading">
        <el-table-column prop="filename" label="文件名" min-width="200">
          <template #default="{ row }">
            <div class="file-name">
              <el-icon v-if="row.file_type === '.pdf'" style="color: #f56c6c"><Document /></el-icon>
              <el-icon v-else-if="row.file_type === '.docx'" style="color: #409eff"><Document /></el-icon>
              <el-icon v-else-if="row.file_type === '.md'" style="color: #67c23a"><Document /></el-icon>
              <el-icon v-else style="color: #909399"><Document /></el-icon>
              <span>{{ row.filename }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="file_size" label="大小" width="100">
          <template #default="{ row }">
            {{ formatSize(row.file_size) }}
          </template>
        </el-table-column>
        <el-table-column prop="entities_count" label="实体" width="80" />
        <el-table-column prop="relations_count" label="关系" width="80" />
        <el-table-column prop="created_at" label="上传时间" width="180">
          <template #default="{ row }">
            {{ formatTime(row.created_at) }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button size="small" @click="viewEntities(row)">实体</el-button>
            <el-button size="small" type="danger" @click="deleteDocument(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- 实体详情对话框 -->
    <el-dialog v-model="showEntities" title="实体详情" width="60%">
      <div v-if="currentDoc">
        <h4>{{ currentDoc.filename }}</h4>
        <el-divider />
        <div class="entities-section">
          <h5>实体列表 ({{ entities.length }})</h5>
          <el-table :data="entities" style="width: 100%">
            <el-table-column prop="name" label="名称" />
            <el-table-column prop="type" label="类型" width="100">
              <template #default="{ row }">
                <el-tag :type="getEntityTypeColor(row.type)">{{ row.type }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="attributes" label="属性">
              <template #default="{ row }">
                <span>{{ JSON.stringify(row.attributes) }}</span>
              </template>
            </el-table-column>
          </el-table>
        </div>
        <el-divider />
        <div class="relations-section">
          <h5>关系列表 ({{ relations.length }})</h5>
          <el-table :data="relations" style="width: 100%">
            <el-table-column prop="source_name" label="源实体" />
            <el-table-column prop="relation" label="关系" />
            <el-table-column prop="target_name" label="目标实体" />
            <el-table-column prop="weight" label="权重" width="80" />
          </el-table>
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { UploadFilled, Document } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import axios from 'axios'

const documents = ref([])
const loading = ref(false)
const showEntities = ref(false)
const currentDoc = ref(null)
const entities = ref([])
const relations = ref([])

onMounted(() => {
  fetchDocuments()
})

async function fetchDocuments() {
  loading.value = true
  try {
    const response = await axios.get('/api/documents')
    documents.value = response.data
  } catch (error) {
    ElMessage.error('获取文档列表失败')
  } finally {
    loading.value = false
  }
}

function beforeUpload(file) {
  const allowedTypes = ['.pdf', '.docx', '.md', '.txt']
  const fileExt = file.name.substring(file.name.lastIndexOf('.')).toLowerCase()

  if (!allowedTypes.includes(fileExt)) {
    ElMessage.error('不支持的文件类型')
    return false
  }

  if (file.size > 50 * 1024 * 1024) {
    ElMessage.error('文件大小不能超过50MB')
    return false
  }

  return true
}

function handleUploadSuccess(response) {
  ElMessage.success('上传成功')
  fetchDocuments()
}

function handleUploadError(error) {
  ElMessage.error('上传失败')
}

async function viewEntities(doc) {
  currentDoc.value = doc
  try {
    const [entitiesRes, relationsRes] = await Promise.all([
      axios.get(`/api/documents/${doc.id}/entities`),
      axios.get(`/api/documents/${doc.id}/relations`)
    ])
    entities.value = entitiesRes.data
    relations.value = relationsRes.data
    showEntities.value = true
  } catch (error) {
    ElMessage.error('获取实体信息失败')
  }
}

async function deleteDocument(doc) {
  try {
    await ElMessageBox.confirm('确定要删除该文档吗？', '确认删除', {
      type: 'warning'
    })

    await axios.delete(`/api/documents/${doc.id}`)
    ElMessage.success('删除成功')
    fetchDocuments()
  } catch (error) {
    if (error !== 'cancel') {
      ElMessage.error('删除失败')
    }
  }
}

function formatSize(bytes) {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}

function formatTime(time) {
  if (!time) return ''
  const date = new Date(time)
  return date.toLocaleString('zh-CN')
}

function getEntityTypeColor(type) {
  const colors = {
    '公司': 'primary',
    '指标': 'success',
    '人物': 'warning',
    '行业': 'info',
    '概念': 'danger'
  }
  return colors[type] || 'info'
}
</script>

<style scoped>
.documents-container {
  background: white;
  border-radius: 8px;
  padding: 20px;
}

.upload-section {
  margin-bottom: 24px;
}

.upload-area {
  width: 100%;
}

.document-list {
  margin-top: 20px;
}

.list-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}

.list-header h3 {
  margin: 0;
}

.file-name {
  display: flex;
  align-items: center;
  gap: 8px;
}

.entities-section,
.relations-section {
  margin-bottom: 16px;
}

.entities-section h5,
.relations-section h5 {
  margin-bottom: 12px;
  color: #606266;
}
</style>
