import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  {
    path: '/',
    redirect: '/chat'
  },
  {
    path: '/chat',
    name: 'Chat',
    component: () => import('../views/ChatView.vue')
  },
  {
    path: '/documents',
    name: 'Documents',
    component: () => import('../views/DocumentsView.vue')
  },
  {
    path: '/knowledge-graph',
    name: 'KnowledgeGraph',
    component: () => import('../views/KnowledgeGraphView.vue')
  },
  {
    path: '/skills',
    name: 'Skills',
    component: () => import('../views/SkillsView.vue')
  },
  {
    path: '/settings',
    name: 'Settings',
    component: () => import('../views/SettingsView.vue')
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

export default router
