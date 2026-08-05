<template>
  <el-container class="app-container">
    <!-- 侧边栏 -->
    <el-aside width="200px" class="app-aside">
      <div class="logo">
        <h2>📊 FinanceWiki</h2>
      </div>
      <el-menu
        :default-active="currentRoute"
        router
        class="aside-menu"
      >
        <el-menu-item index="/chat">
          <el-icon><ChatDotRound /></el-icon>
          <span>对话</span>
        </el-menu-item>
        <el-menu-item index="/documents">
          <el-icon><Document /></el-icon>
          <span>文档管理</span>
        </el-menu-item>
        <el-menu-item index="/knowledge-graph">
          <el-icon><Share /></el-icon>
          <span>知识图谱</span>
        </el-menu-item>
        <el-menu-item index="/skills">
          <el-icon><MagicStick /></el-icon>
          <span>技能管理</span>
        </el-menu-item>
        <el-menu-item index="/settings">
          <el-icon><Setting /></el-icon>
          <span>设置</span>
        </el-menu-item>
      </el-menu>
    </el-aside>

    <!-- 主内容区 -->
    <el-container>
      <el-header class="app-header">
        <div class="header-left">
          <el-breadcrumb separator="/">
            <el-breadcrumb-item :to="{ path: '/' }">首页</el-breadcrumb-item>
            <el-breadcrumb-item>{{ currentTitle }}</el-breadcrumb-item>
          </el-breadcrumb>
        </div>
        <div class="header-right">
          <el-select v-model="currentModel" placeholder="选择模型" size="small">
            <el-option
              v-for="model in models"
              :key="model.provider"
              :label="model.name"
              :value="model.provider"
            />
          </el-select>
        </div>
      </el-header>

      <el-main class="app-main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { ChatDotRound, Document, Share, MagicStick, Setting } from '@element-plus/icons-vue'
import axios from 'axios'

const route = useRoute()
const currentModel = ref('deepseek')
const models = ref([])

const currentRoute = computed(() => route.path)
const currentTitle = computed(() => {
  const titles = {
    '/chat': '对话',
    '/documents': '文档管理',
    '/knowledge-graph': '知识图谱',
    '/skills': '技能管理',
    '/settings': '设置'
  }
  return titles[route.path] || '首页'
})

onMounted(async () => {
  try {
    const response = await axios.get('/api/models')
    models.value = response.data
  } catch (error) {
    console.error('获取模型列表失败:', error)
  }
})
</script>

<style>
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
}

.app-container {
  height: 100vh;
}

.app-aside {
  background: #001529;
  color: white;
}

.logo {
  height: 60px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
}

.logo h2 {
  font-size: 18px;
  color: white;
}

.aside-menu {
  border-right: none;
  background: #001529;
}

.aside-menu .el-menu-item {
  color: rgba(255, 255, 255, 0.65);
}

.aside-menu .el-menu-item:hover {
  color: white;
  background: #1890ff;
}

.aside-menu .el-menu-item.is-active {
  color: white;
  background: #1890ff;
}

.app-header {
  background: white;
  border-bottom: 1px solid #e8e8e8;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 20px;
}

.app-main {
  background: #f0f2f5;
  padding: 20px;
  overflow-y: auto;
}
</style>
