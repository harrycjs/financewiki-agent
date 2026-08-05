<template>
  <div class="knowledge-graph-container">
    <!-- 顶部操作栏 -->
    <div class="toolbar">
      <el-input
        v-model="searchQuery"
        placeholder="搜索实体或关系..."
        clearable
        @keyup.enter="searchKG"
      >
        <template #append>
          <el-button :icon="Search" @click="searchKG" />
        </template>
      </el-input>

      <el-select v-model="filterType" placeholder="筛选类型" clearable>
        <el-option label="公司" value="公司" />
        <el-option label="指标" value="指标" />
        <el-option label="人物" value="人物" />
        <el-option label="行业" value="行业" />
        <el-option label="概念" value="概念" />
      </el-select>
    </div>

    <div class="content-area">
      <!-- 图谱可视化 -->
      <div class="graph-section">
        <div class="section-header">
          <h3>知识图谱</h3>
          <el-tag type="info">
            {{ graphData.nodes.length }} 节点 / {{ graphData.edges.length }} 边
          </el-tag>
        </div>
        <div class="graph-canvas" ref="graphContainer"></div>
      </div>

      <!-- 侧边信息面板 -->
      <div class="info-panel">
        <!-- 统计信息 -->
        <div class="stats-section">
          <h4>统计信息</h4>
          <div class="stats-grid">
            <div class="stat-item">
              <div class="stat-value">{{ stats.total_entities || 0 }}</div>
              <div class="stat-label">实体总数</div>
            </div>
            <div class="stat-item">
              <div class="stat-value">{{ stats.total_relations || 0 }}</div>
              <div class="stat-label">关系总数</div>
            </div>
            <div class="stat-item">
              <div class="stat-value">{{ stats.total_documents || 0 }}</div>
              <div class="stat-label">文档总数</div>
            </div>
          </div>
        </div>

        <!-- 实体类型分布 -->
        <div class="types-section">
          <h4>实体类型分布</h4>
          <div class="type-list">
            <div
              v-for="(count, type) in stats.entity_types"
              :key="type"
              class="type-item"
            >
              <el-tag :type="getEntityTypeColor(type)">{{ type }}</el-tag>
              <span class="count">{{ count }}</span>
            </div>
          </div>
        </div>

        <!-- 选中实体详情 -->
        <div v-if="selectedEntity" class="entity-detail">
          <h4>实体详情</h4>
          <el-descriptions :column="1" border size="small">
            <el-descriptions-item label="名称">{{ selectedEntity.name }}</el-descriptions-item>
            <el-descriptions-item label="类型">
              <el-tag :type="getEntityTypeColor(selectedEntity.type)">{{ selectedEntity.type }}</el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="属性">
              <pre>{{ JSON.stringify(selectedEntity.attributes, null, 2) }}</pre>
            </el-descriptions-item>
          </el-descriptions>

          <h5 style="margin-top: 16px;">关联关系</h5>
          <div v-if="entityRelations.length === 0" class="no-relations">
            暂无关联关系
          </div>
          <div v-else class="relation-list">
            <div
              v-for="rel in entityRelations"
              :key="rel.id"
              class="relation-item"
            >
              <span class="related-name">{{ rel.related_name }}</span>
              <el-tag size="small">{{ rel.relation }}</el-tag>
              <span class="direction">{{ rel.direction === 'outgoing' ? '→' : '←' }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, nextTick, watch } from 'vue'
import { Search } from '@element-plus/icons-vue'
import { Network } from 'vis-network'
import { DataSet } from 'vis-data'
import axios from 'axios'

const searchQuery = ref('')
const filterType = ref('')
const graphContainer = ref(null)
const graphData = ref({ nodes: [], edges: [] })
const stats = ref({})
const selectedEntity = ref(null)
const entityRelations = ref([])
let network = null

onMounted(async () => {
  await fetchKGData()
  await fetchStats()
  // 等待DOM更新后再初始化图
  nextTick(() => {
    initGraph()
  })
})

async function fetchKGData() {
  try {
    const response = await axios.get('/api/knowledge-graph')
    graphData.value = response.data
    updateGraph()
  } catch (error) {
    console.error('获取知识图谱数据失败:', error)
  }
}

async function fetchStats() {
  try {
    const response = await axios.get('/api/knowledge-graph/stats')
    stats.value = response.data
  } catch (error) {
    console.error('获取统计信息失败:', error)
  }
}

function initGraph() {
  if (!graphContainer.value) return

  const nodes = new DataSet(formatNodes(graphData.value.nodes))
  const edges = new DataSet(formatEdges(graphData.value.edges))

  const options = {
    nodes: {
      shape: 'dot',
      size: 20,
      font: {
        size: 12,
        color: '#333'
      },
      borderWidth: 2
    },
    edges: {
      arrows: 'to',
      font: {
        size: 10,
        align: 'middle'
      },
      color: {
        color: '#848484',
        highlight: '#1890ff'
      }
    },
    physics: {
      enabled: true,
      solver: 'forceAtlas2Based',
      forceAtlas2Based: {
        gravitationalConstant: -50,
        centralGravity: 0.005,
        springLength: 150,
        springConstant: 0.08
      },
      stabilization: {
        iterations: 100
      },
      // 限制最大速度，避免节点震荡永不收敛
      maxVelocity: 30,
      minVelocity: 0.75,
      timestep: 0.35
    },
    interaction: {
      hover: true,
      tooltipDelay: 200
    }
  }

  network = new Network(graphContainer.value, { nodes, edges }, options)

  // 点击事件
  network.on('click', async (params) => {
    if (params.nodes.length > 0) {
      const nodeId = params.nodes[0]
      const node = graphData.value.nodes.find(n => n.id === nodeId)
      if (node) {
        selectedEntity.value = node
        await fetchEntityRelations(node.name)
      }
    } else {
      selectedEntity.value = null
      entityRelations.value = []
    }
  })
}

function formatNodes(nodes) {
  const typeColors = {
    '公司': '#409eff',
    '指标': '#67c23a',
    '人物': '#e6a23c',
    '行业': '#909399',
    '概念': '#f56c6c'
  }

  return nodes.map(node => ({
    id: node.id,
    label: node.name,
    color: typeColors[node.type] || '#909399',
    title: `${node.name} (${node.type})`
  }))
}

function formatEdges(edges) {
  return edges.map(edge => ({
    id: edge.id,
    from: edge.source,
    to: edge.target,
    label: edge.relation,
    title: `${edge.source_name} → ${edge.target_name}: ${edge.relation}`
  }))
}

function updateGraph() {
  if (network) {
    const nodes = new DataSet(formatNodes(graphData.value.nodes))
    const edges = new DataSet(formatEdges(graphData.value.edges))
    network.setData({ nodes, edges })
  }
}

async function fetchEntityRelations(entityName) {
  try {
    const response = await axios.get(`/api/knowledge-graph/entity/${encodeURIComponent(entityName)}`)
    entityRelations.value = response.data.relations || []
  } catch (error) {
    console.error('获取实体关系失败:', error)
    entityRelations.value = []
  }
}

async function searchKG() {
  if (!searchQuery.value.trim()) {
    await fetchKGData()
    return
  }

  try {
    const params = { q: searchQuery.value }
    if (filterType.value) {
      params.entity_type = filterType.value
    }
    const response = await axios.get('/api/knowledge-graph/search', { params })
    graphData.value = {
      nodes: response.data.entities || [],
      edges: (response.data.relations || []).map(r => ({
        id: r.id,
        source: r.source_name,
        target: r.target_name,
        relation: r.relation,
        source_name: r.source_name,
        target_name: r.target_name
      }))
    }
    updateGraph()
  } catch (error) {
    console.error('搜索失败:', error)
  }
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
.knowledge-graph-container {
  background: white;
  border-radius: 8px;
  padding: 20px;
  height: calc(100vh - 120px);
  display: flex;
  flex-direction: column;
}

.toolbar {
  display: flex;
  gap: 16px;
  margin-bottom: 20px;
}

.toolbar .el-input {
  width: 300px;
}

.content-area {
  flex: 1;
  display: flex;
  gap: 20px;
  overflow: hidden;
}

.graph-section {
  flex: 1;
  display: flex;
  flex-direction: column;
  border: 1px solid #e8e8e8;
  border-radius: 8px;
  overflow: hidden;
  min-height: 400px;
}

.section-header {
  padding: 12px 16px;
  border-bottom: 1px solid #e8e8e8;
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: #fafafa;
}

.section-header h3 {
  margin: 0;
}

.graph-canvas {
  flex: 1;
  min-height: 350px;
  background: #fafafa;
}

.info-panel {
  width: 320px;
  border: 1px solid #e8e8e8;
  border-radius: 8px;
  overflow-y: auto;
  padding: 16px;
}

.stats-section,
.types-section,
.entity-detail {
  margin-bottom: 24px;
}

.stats-section h4,
.types-section h4,
.entity-detail h4 {
  margin: 0 0 12px 0;
  font-size: 14px;
  color: #606266;
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
}

.stat-item {
  text-align: center;
  padding: 12px;
  background: #f5f7fa;
  border-radius: 8px;
}

.stat-value {
  font-size: 24px;
  font-weight: bold;
  color: #409eff;
}

.stat-label {
  font-size: 12px;
  color: #909399;
  margin-top: 4px;
}

.type-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.type-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.count {
  color: #909399;
}

.entity-detail pre {
  background: #f5f7fa;
  padding: 8px;
  border-radius: 4px;
  font-size: 12px;
  margin: 0;
  white-space: pre-wrap;
  word-break: break-all;
}

.no-relations {
  color: #909399;
  font-size: 12px;
}

.relation-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.relation-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px;
  background: #f5f7fa;
  border-radius: 4px;
}

.related-name {
  font-weight: 500;
}

.direction {
  color: #909399;
}
</style>
