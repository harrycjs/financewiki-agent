import { defineStore } from 'pinia'
import { ref } from 'vue'
import axios from 'axios'

export const useChatStore = defineStore('chat', () => {
  const sessions = ref([])
  const currentSession = ref(null)
  const messages = ref([])
  const loading = ref(false)

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

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        const chunk = decoder.decode(value)
        const lines = chunk.split('\n').filter(line => line.trim())

        for (const line of lines) {
          try {
            const data = JSON.parse(line)

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
            } else if (data.type === 'content') {
              // 第一段内容到达时 push 助手消息，之后追加
              const last = messages.value[messages.value.length - 1]
              if (last && last.role === 'assistant' && last.__streaming) {
                // 已有流式占位，累加内容
                last.content = data.content
                messages.value = [...messages.value]
              } else {
                // 第一次内容到达，新建助手消息
                messages.value.push({
                  role: 'assistant',
                  content: data.content,
                  sources: data.sources || [],
                  __streaming: true
                })
              }
            }
          } catch (e) {
            // 忽略解析错误
          }
        }
      }

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
    fetchSessions,
    createSession,
    sendMessage,
    fetchHistory,
    deleteSession
  }
})
