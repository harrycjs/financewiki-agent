import { defineStore } from 'pinia'
import { ref } from 'vue'
import axios from 'axios'

export const useChatStore = defineStore('chat', () => {
  const sessions = ref([])
  const currentSession = ref(null)
  const messages = ref([])
  const loading = ref(false)
  // 首个 delta 到达后置 true，用于让"正在输入"指示器让位给真正的流式内容
  const streaming = ref(false)

  // 获取会话列表
  async function fetchSessions() {
    try {
      const response = await axios.get('/api/chat/sessions')
      sessions.value = response.data
    } catch (error) {
      console.error('获取会话列表失败:', error)
    }
  }

  // 创建新会话
  async function createSession(title = null) {
    try {
      const response = await axios.post('/api/chat/sessions', { title })
      const newSession = response.data
      sessions.value.unshift(newSession)
      currentSession.value = newSession
      messages.value = []
      return newSession
    } catch (error) {
      console.error('创建会话失败:', error)
      throw error
    }
  }

  // 发送消息
  async function sendMessage(content, model = 'deepseek') {
    loading.value = true
    streaming.value = false

    try {
      // 只 push 用户消息；助手消息等真正收到内容时再 push
      // （避免显示一个空 assistant 气泡 + loading 指示器，两个占位同时出现）
      messages.value.push({
        role: 'user',
        content: content
      })

      // 发送到后端
      const response = await fetch('/api/chat', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          message: content,
          session_id: currentSession.value?.id,
          model: model,
          top_k: 10
        })
      })

      // 处理流式响应
      const reader = response.body.getReader()
      const decoder = new TextDecoder()

      let returnedSessionId = null
      // 半行缓冲：delta 变小之后，一个 JSON 对象很容易被切在两个 chunk 中间，
      // 必须把不完整的尾巴留到下一轮再拼。decode 也要开 stream:true，
      // 否则中文的多字节序列跨 chunk 会解出乱码。
      let buffer = ''

      const ensureAssistant = () => {
        const last = messages.value[messages.value.length - 1]
        if (last && last.role === 'assistant' && last.__streaming) return last
        const created = {
          role: 'assistant',
          content: '',
          sources: [],
          tools: [],
          __streaming: true
        }
        messages.value.push(created)
        return created
      }

      const handleEvent = (data) => {
        if (data.type === 'session_id' && data.session_id) {
          returnedSessionId = data.session_id
          // ★ 修复：后端可能新建了会话，立即把 session 信息同步到本地 store
          if (!currentSession.value || currentSession.value.id !== data.session_id) {
            currentSession.value = {
              id: data.session_id,
              title: content.trim().replace(/\n/g, ' ').slice(0, 30) +
                (content.trim().length > 30 ? '...' : ''),
              model: model
            }
          }
        } else if (data.type === 'delta') {
          streaming.value = true
          const msg = ensureAssistant()
          msg.content += data.content
          messages.value = [...messages.value]
        } else if (data.type === 'tool_call') {
          streaming.value = true
          const msg = ensureAssistant()
          msg.tools.push({ name: data.name, arguments: data.arguments, status: 'running' })
          messages.value = [...messages.value]
        } else if (data.type === 'tool_result') {
          const msg = ensureAssistant()
          const entry = [...msg.tools].reverse().find(t => t.name === data.name && t.status === 'running')
          if (entry) {
            entry.status = data.ok ? 'done' : 'failed'
            entry.preview = data.preview
          }
          messages.value = [...messages.value]
        } else if (data.type === 'done') {
          const msg = ensureAssistant()
          msg.sources = data.sources || []
          messages.value = [...messages.value]
        } else if (data.type === 'error') {
          const msg = ensureAssistant()
          msg.content += `\n\n> ⚠️ 生成中断：${data.message}`
          messages.value = [...messages.value]
        }
      }

      const drain = (line) => {
        if (!line.trim()) return
        try {
          handleEvent(JSON.parse(line))
        } catch (e) {
          // 忽略解析错误
        }
      }

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() ?? ''
        for (const line of lines) drain(line)
      }
      drain(buffer)

      // 流式结束：去掉 __streaming 标记
      const last = messages.value[messages.value.length - 1]
      if (last && last.__streaming) {
        delete last.__streaming
        messages.value = [...messages.value]
      }

      // ★ 修复：消息发完后刷新侧边栏，让新会话出现 / 老会话 updated_at 更新到顶端
      await fetchSessions()

      // 确保 currentSession 在列表里（如果是后端新建的会话）
      if (returnedSessionId && currentSession.value?.id === returnedSessionId) {
        const exists = sessions.value.find(s => s.id === returnedSessionId)
        if (!exists) {
          sessions.value.unshift({
            id: returnedSessionId,
            title: currentSession.value.title,
            model: model,
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString()
          })
        } else {
          // 已有则更新 updated_at 让它排到顶
          sessions.value = [
            { ...exists, updated_at: new Date().toISOString() },
            ...sessions.value.filter(s => s.id !== returnedSessionId)
          ]
        }
      }

    } catch (error) {
      console.error('发送消息失败:', error)
      // 移除流式占位，添加错误消息
      const last = messages.value[messages.value.length - 1]
      if (last && last.__streaming) {
        messages.value.pop()
      }
      messages.value.push({
        role: 'assistant',
        content: '抱歉，发送消息时出现错误，请稍后重试。'
      })
    } finally {
      loading.value = false
      streaming.value = false
    }
  }

  // 获取对话历史
  async function fetchHistory(sessionId) {
    try {
      const response = await axios.get('/api/chat/history', {
        params: { session_id: sessionId }
      })
      messages.value = response.data
    } catch (error) {
      console.error('获取对话历史失败:', error)
    }
  }

  // 删除会话
  async function deleteSession(sessionId) {
    try {
      await axios.delete(`/api/chat/sessions/${sessionId}`)
      sessions.value = sessions.value.filter(s => s.id !== sessionId)
      if (currentSession.value?.id === sessionId) {
        currentSession.value = null
        messages.value = []
      }
    } catch (error) {
      console.error('删除会话失败:', error)
    }
  }

  return {
    sessions,
    currentSession,
    messages,
    loading,
    streaming,
    fetchSessions,
    createSession,
    sendMessage,
    fetchHistory,
    deleteSession
  }
})
