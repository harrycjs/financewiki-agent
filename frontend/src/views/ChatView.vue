<template>
  <div class="chat-container">
    <!-- 左侧会话列表 -->
    <div class="session-list">
      <div class="session-header">
        <h3>会话列表</h3>
        <el-button type="primary" size="small" @click="createNewSession">
          <el-icon><Plus /></el-icon> 新建
        </el-button>
      </div>
      <div class="session-items">
        <div
          v-for="session in sessions"
          :key="session.id"
          class="session-item"
          :class="{ active: currentSession?.id === session.id }"
          @click="selectSession(session)"
        >
          <div class="session-title">{{ session.title || '新会话' }}</div>
          <div class="session-time">{{ formatTime(session.created_at) }}</div>
          <el-button
            type="danger"
            size="small"
            class="delete-btn"
            @click.stop="deleteSession(session.id)"
          >
            <el-icon><Delete /></el-icon>
          </el-button>
        </div>
      </div>
    </div>

    <!-- 右侧对话区 -->
    <div class="chat-main">
      <!-- 消息列表 -->
      <div class="message-list" ref="messageListRef">
        <div v-if="messages.length === 0" class="empty-state">
          <el-icon :size="48"><ChatDotRound /></el-icon>
          <p>开始新的对话</p>
        </div>
        <div
          v-for="(msg, index) in messages"
          :key="index"
          class="message"
          :class="msg.role"
        >
          <div class="message-avatar">
            <el-avatar v-if="msg.role === 'user'" :size="36">我</el-avatar>
            <el-avatar v-else :size="36" style="background: #1890ff">AI</el-avatar>
          </div>
          <div class="message-content">
            <div v-if="msg.tools && msg.tools.length > 0" class="message-tools">
              <el-tag
                v-for="(tool, ti) in msg.tools"
                :key="ti"
                size="small"
                :type="tool.status === 'failed' ? 'danger' : (tool.status === 'done' ? 'success' : 'warning')"
                effect="plain"
                class="tool-chip"
              >
                <el-icon v-if="tool.status === 'running'" class="is-loading"><Loading /></el-icon>
                {{ tool.name }}
                <span v-if="tool.arguments" class="tool-arg">{{ summarizeArgs(tool.arguments) }}</span>
              </el-tag>
            </div>
            <div class="message-text" v-html="renderMarkdown(msg.content)"></div>
            <div v-if="msg.sources && msg.sources.length > 0" class="message-sources">
              <el-tag size="small" type="info" v-for="source in msg.sources" :key="source">
                {{ source }}
              </el-tag>
            </div>
          </div>
        </div>
        <div v-if="loading && !streaming" class="message assistant">
          <div class="message-avatar">
            <el-avatar :size="36" style="background: #1890ff">AI</el-avatar>
          </div>
          <div class="message-content">
            <div class="typing-indicator">
              <span></span>
              <span></span>
              <span></span>
            </div>
          </div>
        </div>
      </div>

      <!-- 输入区 -->
      <div class="input-area">
        <div class="model-selector">
          <el-select v-model="selectedModel" placeholder="选择模型" size="small">
            <el-option
              v-for="model in models"
              :key="model.provider"
              :label="model.name"
              :value="model.provider"
            />
          </el-select>
        </div>
        <div class="input-box">
          <el-input
            v-model="inputMessage"
            type="textarea"
            :rows="2"
            placeholder="输入您的问题..."
            @keydown.enter.exact.prevent="sendMessage"
            :disabled="loading"
          />
          <el-button
            type="primary"
            :icon="Promotion"
            :loading="loading"
            @click="sendMessage"
            :disabled="!inputMessage.trim()"
          >
            发送
          </el-button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, nextTick, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { Plus, Delete, ChatDotRound, Promotion, Loading } from '@element-plus/icons-vue'
import { useChatStore } from '../stores/chat'
import { marked } from 'marked'
import axios from 'axios'

const chatStore = useChatStore()
const { sessions, currentSession, messages, loading, streaming } = storeToRefs(chatStore)

const inputMessage = ref('')
const selectedModel = ref('deepseek')
const models = ref([])
const messageListRef = ref(null)

onMounted(async () => {
  await chatStore.fetchSessions()
  await fetchModels()
})

// 监听消息变化，滚动到底部
watch(messages, () => {
  nextTick(() => {
    if (messageListRef.value) {
      messageListRef.value.scrollTop = messageListRef.value.scrollHeight
    }
  })
}, { deep: true })

async function fetchModels() {
  try {
    const response = await axios.get('/api/models')
    models.value = response.data.filter(m => m.has_api_key)
  } catch (error) {
    console.error('获取模型列表失败:', error)
  }
}

async function createNewSession() {
  await chatStore.createSession()
}

async function selectSession(session) {
  chatStore.currentSession = session
  await chatStore.fetchHistory(session.id)
}

async function deleteSession(sessionId) {
  await chatStore.deleteSession(sessionId)
}

async function sendMessage() {
  if (!inputMessage.value.trim() || loading.value) return

  const message = inputMessage.value
  inputMessage.value = ''

  await chatStore.sendMessage(message, selectedModel.value)
}

function renderMarkdown(text) {
  if (!text) return ''
  return marked.parse(text)
}

// 工具参数在气泡上只展示一小段，完整参数没必要占版面
function summarizeArgs(args) {
  if (!args || typeof args !== 'object') return ''
  const primary = args.command ?? args.query ?? args.path
  if (!primary) return ''
  const text = String(primary)
  return text.length > 40 ? `: ${text.slice(0, 40)}…` : `: ${text}`
}

function formatTime(time) {
  if (!time) return ''
  const date = new Date(time)
  return date.toLocaleString('zh-CN')
}
</script>

<style scoped>
.chat-container {
  display: flex;
  height: calc(100vh - 120px);
  background: white;
  border-radius: 8px;
  overflow: hidden;
}

.session-list {
  width: 260px;
  border-right: 1px solid #e8e8e8;
  display: flex;
  flex-direction: column;
}

.session-header {
  padding: 16px;
  border-bottom: 1px solid #e8e8e8;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.session-header h3 {
  margin: 0;
  font-size: 16px;
}

.session-items {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
}

.session-item {
  padding: 12px;
  border-radius: 8px;
  cursor: pointer;
  margin-bottom: 8px;
  position: relative;
  transition: background 0.2s;
}

.session-item:hover {
  background: #f5f5f5;
}

.session-item.active {
  background: #e6f7ff;
}

.session-title {
  font-size: 14px;
  font-weight: 500;
  margin-bottom: 4px;
  padding-right: 30px;
}

.session-time {
  font-size: 12px;
  color: #999;
}

.delete-btn {
  position: absolute;
  right: 8px;
  top: 50%;
  transform: translateY(-50%);
  opacity: 0;
  transition: opacity 0.2s;
}

.session-item:hover .delete-btn {
  opacity: 1;
}

.chat-main {
  flex: 1;
  display: flex;
  flex-direction: column;
}

.message-list {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
}

.empty-state {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: #999;
}

.empty-state p {
  margin-top: 16px;
}

.message {
  display: flex;
  margin-bottom: 20px;
}

.message.user {
  flex-direction: row-reverse;
}

.message-avatar {
  margin: 0 12px;
}

.message-content {
  max-width: 70%;
}

.message.user .message-content {
  background: #1890ff;
  color: white;
  border-radius: 12px 12px 0 12px;
  padding: 12px 16px;
}

.message.assistant .message-content {
  background: #f5f5f5;
  border-radius: 12px 12px 12px 0;
  padding: 12px 16px;
}

.message-text {
  line-height: 1.6;
}

.message-text :deep(p) {
  margin: 0 0 8px 0;
}

.message-text :deep(p:last-child) {
  margin-bottom: 0;
}

.message-sources {
  margin-top: 8px;
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.message-tools {
  margin-bottom: 8px;
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.tool-chip {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
}

.tool-arg {
  opacity: 0.7;
}

.typing-indicator {
  display: flex;
  gap: 4px;
}

.typing-indicator span {
  width: 8px;
  height: 8px;
  background: #999;
  border-radius: 50%;
  animation: typing 1.4s infinite ease-in-out;
}

.typing-indicator span:nth-child(2) {
  animation-delay: 0.2s;
}

.typing-indicator span:nth-child(3) {
  animation-delay: 0.4s;
}

@keyframes typing {
  0%, 80%, 100% {
    transform: scale(0.8);
    opacity: 0.5;
  }
  40% {
    transform: scale(1);
    opacity: 1;
  }
}

.input-area {
  padding: 16px;
  border-top: 1px solid #e8e8e8;
  background: white;
}

.model-selector {
  margin-bottom: 12px;
}

.input-box {
  display: flex;
  gap: 12px;
  align-items: flex-end;
}

.input-box .el-input {
  flex: 1;
}
</style>
