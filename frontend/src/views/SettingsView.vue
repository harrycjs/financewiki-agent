<template>
  <div class="settings-container">
    <el-card class="settings-card">
      <template #header>
        <div class="card-header">
          <h3>模型配置</h3>
        </div>
      </template>

      <el-table :data="models" style="width: 100%">
        <el-table-column prop="name" label="模型名称" width="150" />
        <el-table-column prop="provider" label="提供商" width="120" />
        <el-table-column label="API Key" width="200">
          <template #default="{ row }">
            <span v-if="row.has_api_key">已配置</span>
            <span v-else class="not-configured">未配置</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="row.is_active ? 'success' : 'info'">
              {{ row.is_active ? '使用中' : '未使用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="250">
          <template #default="{ row }">
            <el-button size="small" @click="openConfigDialog(row)">配置</el-button>
            <el-button
              size="small"
              type="primary"
              :disabled="row.is_active"
              @click="switchModel(row.provider)"
            >
              切换
            </el-button>
            <el-button
              size="small"
              :loading="testingModel === row.provider"
              @click="testConnection(row.provider)"
            >
              测试
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 配置对话框 -->
    <el-dialog v-model="showConfigDialog" title="模型配置" width="500px">
      <el-form :model="configForm" label-width="100px">
        <el-form-item label="提供商">
          <el-input v-model="configForm.provider" disabled />
        </el-form-item>
        <el-form-item label="API Key">
          <el-input
            v-model="configForm.api_key"
            type="password"
            show-password
            placeholder="请输入API Key"
          />
        </el-form-item>
        <el-form-item label="API Base">
          <el-input
            v-model="configForm.api_base"
            placeholder="API地址（可选）"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showConfigDialog = false">取消</el-button>
        <el-button type="primary" @click="saveConfig">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import axios from 'axios'

const models = ref([])
const showConfigDialog = ref(false)
const configForm = ref({
  provider: '',
  api_key: '',
  api_base: ''
})
const testingModel = ref(null)

onMounted(() => {
  fetchModels()
})

async function fetchModels() {
  try {
    const response = await axios.get('/api/models')
    models.value = response.data
  } catch (error) {
    ElMessage.error('获取模型列表失败')
  }
}

function openConfigDialog(model) {
  configForm.value = {
    provider: model.provider,
    api_key: '',
    api_base: model.api_base || ''
  }
  showConfigDialog.value = true
}

async function saveConfig() {
  try {
    await axios.post(`/api/models/config/${configForm.value.provider}`, {
      api_key: configForm.value.api_key,
      api_base: configForm.value.api_base
    })
    ElMessage.success('配置保存成功')
    showConfigDialog.value = false
    fetchModels()
  } catch (error) {
    ElMessage.error('保存配置失败')
  }
}

async function switchModel(provider) {
  try {
    await axios.post('/api/models/switch', { model_name: provider })
    ElMessage.success('模型切换成功')
    fetchModels()
  } catch (error) {
    ElMessage.error('切换模型失败')
  }
}

async function testConnection(provider) {
  testingModel.value = provider
  try {
    const response = await axios.post(`/api/models/test/${provider}`)
    if (response.data.success) {
      ElMessage.success('连接测试成功')
    } else {
      ElMessage.error(response.data.message)
    }
  } catch (error) {
    ElMessage.error('连接测试失败')
  } finally {
    testingModel.value = null
  }
}
</script>

<style scoped>
.settings-container {
  background: white;
  border-radius: 8px;
  padding: 20px;
}

.settings-card {
  margin-bottom: 20px;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.card-header h3 {
  margin: 0;
}

.not-configured {
  color: #f56c6c;
}
</style>
