<template>
  <div class="skills-container">
    <el-card>
      <template #header>
        <div class="card-header">
          <h3>技能管理（Skills）</h3>
          <div style="display: flex; gap: 8px;">
            <el-button type="primary" @click="openCreateDialog">
              <el-icon><Plus /></el-icon> 新建技能
            </el-button>
            <el-button @click="reloadSkills">
              <el-icon><Refresh /></el-icon> 重新扫描
            </el-button>
          </div>
        </div>
      </template>

      <div class="hint">
        技能 = <code>backend/skills/&lt;name&gt;/SKILL.md</code> 文件夹。
        启动时自动扫描加载；启用后 AI 在对话中按关键词预筛按需加载完整指令。
      </div>

      <el-table :data="skills" style="width: 100%" v-loading="loading">
        <el-table-column prop="title" label="名称" min-width="140" />
        <el-table-column label="标识" min-width="180">
          <template #default="{ row }">
            <el-tag size="small" type="info">{{ row.name }}</el-tag>
            <el-tag v-if="row.is_preset" size="small" type="warning" style="margin-left: 4px">预置</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="分类" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="categoryColor(row.category)">{{ row.category }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="描述" min-width="200">
          <template #default="{ row }">
            <span class="desc-cell">{{ row.description || '(无描述)' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="触发词" min-width="200">
          <template #default="{ row }">
            <el-tag
              v-for="kw in (row.trigger_keywords || []).slice(0, 5)"
              :key="kw"
              size="small"
              style="margin-right: 4px; margin-bottom: 4px"
            >
              {{ kw }}
            </el-tag>
            <span v-if="(row.trigger_keywords || []).length > 5" class="more-kw">
              +{{ row.trigger_keywords.length - 5 }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="启用" width="80" align="center">
          <template #default="{ row }">
            <el-switch
              :model-value="row.enabled"
              @change="toggleSkill(row)"
              :disabled="loading"
            />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="240" fixed="right">
          <template #default="{ row }">
            <el-button size="small" @click="openEditDialog(row)">编辑</el-button>
            <el-button size="small" type="success" @click="openTestDialog(row)">测试</el-button>
            <el-button
              size="small"
              type="danger"
              :disabled="row.is_preset"
              @click="deleteSkill(row)"
            >
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 新建/编辑 对话框（共享） -->
    <el-dialog
      v-model="showEditDialog"
      :title="editingId ? '编辑技能' : '新建技能'"
      width="720px"
      :close-on-click-modal="false"
    >
      <el-form :model="form" label-width="100px">
        <el-form-item label="标识 (name)" required>
          <el-input
            v-model="form.name"
            :disabled="!!editingId"
            placeholder="英文小写/数字/连字符，如 financial-ratio-analysis"
          />
          <div class="form-hint">
            {{ editingId ? 'name 是文件夹名，不可修改' : 'name 将作为文件夹名，保存后不可改' }}
          </div>
        </el-form-item>
        <el-form-item label="SKILL.md 内容" required>
          <el-input
            v-model="form.raw"
            type="textarea"
            :rows="18"
            style="font-family: 'Cascadia Code', 'Courier New', monospace; font-size: 12px"
            placeholder="YAML frontmatter + Markdown 正文"
          />
          <div class="form-hint">
            第一段 <code>---</code> 之间是 YAML frontmatter（必填字段：name / title / description / enabled），其余是 Markdown 正文。
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showEditDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveSkill">保存</el-button>
      </template>
    </el-dialog>

    <!-- 测试对话框：调 /test 返回完整 system_prompt 预览 -->
    <el-dialog
      v-model="showTestDialog"
      :title="`测试：${testingSkill?.title || ''}`"
      width="820px"
      :close-on-click-modal="false"
    >
      <el-form label-width="80px">
        <el-form-item label="示例问题">
          <el-input
            v-model="testQuery"
            type="textarea"
            :rows="2"
            placeholder="输入一个示例问题，看技能命中后的 system_prompt 长什么样"
          />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="runTest" :loading="testing">生成预览</el-button>
        </el-form-item>
      </el-form>

      <div v-if="testResult" class="test-result">
        <div class="test-stats">
          <el-tag>命中技能: {{ testResult.selected_skills.join(', ') || '(无)' }}</el-tag>
          <el-tag type="info">索引: {{ testResult.index_length }} 字符</el-tag>
          <el-tag type="success">完整 instructions: {{ testResult.full_instructions_length }} 字符</el-tag>
          <el-tag type="warning">完整 prompt: {{ testResult.system_prompt_length }} 字符</el-tag>
        </div>
        <pre>{{ testResult.system_prompt }}</pre>
      </div>

      <template #footer>
        <el-button @click="showTestDialog = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { Plus, Refresh } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import axios from 'axios'

const skills = ref([])
const loading = ref(false)
const showEditDialog = ref(false)
const showTestDialog = ref(false)
const editingId = ref(null)
const saving = ref(false)
const testing = ref(false)
const testingSkill = ref(null)
const testQuery = ref('')
const testResult = ref(null)

const form = ref({
  name: '',
  raw: '',
})

const PRESET_TEMPLATE = `---
name: my-skill
title: 我的技能
description: 一句话说明这个技能做什么
category: general
trigger_keywords: [关键词1, 关键词2]
enabled: true
---

# 技能正文（Markdown）

## 适用场景
什么时候使用此技能。

## 分析要点
1. ...

## 输出要求
- ...
`

// ---------- 列表 ----------

async function fetchSkills() {
  loading.value = true
  try {
    const r = await axios.get('/api/skills')
    skills.value = r.data
  } catch (e) {
    ElMessage.error('获取技能列表失败')
  } finally {
    loading.value = false
  }
}

async function reloadSkills() {
  loading.value = true
  try {
    await axios.post('/api/skills/reload')
    await fetchSkills()
    ElMessage.success('已重新扫描')
  } catch (e) {
    ElMessage.error('重新扫描失败')
  } finally {
    loading.value = false
  }
}

// ---------- toggle ----------

async function toggleSkill(row) {
  try {
    const r = await axios.post(`/api/skills/${row.name}/toggle`)
    row.enabled = r.data.enabled
    ElMessage.success(`${row.name} 已${r.data.enabled ? '启用' : '关闭'}`)
  } catch (e) {
    ElMessage.error('切换失败')
    await fetchSkills()
  }
}

// ---------- 新建/编辑 ----------

function openCreateDialog() {
  editingId.value = null
  form.value = {
    name: '',
    raw: PRESET_TEMPLATE,
  }
  showEditDialog.value = true
}

async function openEditDialog(row) {
  editingId.value = row.name
  try {
    const r = await axios.get(`/api/skills/${row.name}`)
    form.value = {
      name: r.data.name,
      raw: r.data.raw,
    }
    showEditDialog.value = true
  } catch (e) {
    ElMessage.error('读取技能失败')
  }
}

async function saveSkill() {
  if (!form.value.name || !form.value.raw) {
    ElMessage.error('name 和内容必填')
    return
  }
  saving.value = true
  try {
    if (editingId.value) {
      await axios.put(`/api/skills/${editingId.value}`, { content: form.value.raw })
      ElMessage.success('已保存')
    } else {
      await axios.post('/api/skills', {
        name: form.value.name,
        content: form.value.raw,
      })
      ElMessage.success('技能创建成功')
    }
    showEditDialog.value = false
    await fetchSkills()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}

// ---------- 删除 ----------

async function deleteSkill(row) {
  try {
    await ElMessageBox.confirm(
      `确认删除技能 "${row.title}" 吗？该操作不可恢复。`,
      '确认删除',
      { type: 'warning' }
    )
    await axios.delete(`/api/skills/${row.name}`)
    ElMessage.success('已删除')
    await fetchSkills()
  } catch (e) {
    if (e !== 'cancel') {
      ElMessage.error(e?.response?.data?.detail || '删除失败')
    }
  }
}

// ---------- 测试 ----------

function openTestDialog(row) {
  testingSkill.value = row
  testQuery.value = ''
  testResult.value = null
  showTestDialog.value = true
}

async function runTest() {
  if (!testQuery.value.trim()) {
    ElMessage.error('请输入示例问题')
    return
  }
  testing.value = true
  try {
    const r = await axios.post(`/api/skills/${testingSkill.value.name}/test`, {
      query: testQuery.value,
    })
    testResult.value = r.data
  } catch (e) {
    ElMessage.error('测试失败')
  } finally {
    testing.value = false
  }
}

// ---------- 辅助 ----------

function categoryColor(c) {
  return {
    analysis: 'primary',
    writing: 'success',
    retrieval: 'warning',
    general: 'info',
  }[c] || 'info'
}

onMounted(fetchSkills)
</script>

<style scoped>
.skills-container {
  background: white;
  border-radius: 8px;
  padding: 20px;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.card-header h3 {
  margin: 0;
  font-size: 16px;
}

.hint {
  background: #f5f7fa;
  padding: 8px 12px;
  border-radius: 4px;
  font-size: 13px;
  color: #606266;
  margin-bottom: 16px;
}

.hint code {
  background: #e6e8eb;
  padding: 1px 4px;
  border-radius: 3px;
  font-size: 12px;
}

.desc-cell {
  display: inline-block;
  max-width: 240px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: middle;
}

.more-kw {
  color: #909399;
  font-size: 12px;
  margin-left: 4px;
}

.form-hint {
  font-size: 12px;
  color: #909399;
  margin-top: 4px;
  line-height: 1.5;
}

.form-hint code {
  background: #e6e8eb;
  padding: 1px 4px;
  border-radius: 3px;
}

.test-result {
  margin-top: 12px;
  border-top: 1px solid #e8e8e8;
  padding-top: 12px;
}

.test-stats {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}

.test-result pre {
  background: #f5f7fa;
  border: 1px solid #e6e8eb;
  border-radius: 4px;
  padding: 12px;
  font-size: 12px;
  max-height: 400px;
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-word;
  margin: 0;
  font-family: 'Cascadia Code', 'Courier New', monospace;
}
</style>