<template>
  <div class="board-view">
    <div class="page-header">
      <h2 class="page-title">任务看板</h2>
      <div class="header-actions">
        <el-button @click="refresh" :icon="Refresh">刷新</el-button>
      </div>
    </div>

    <div class="kanban-board">
      <div
        v-for="col in columns"
        :key="col.key"
        class="kanban-col"
      >
        <div class="kanban-col-header" :style="{ borderColor: col.color, color: col.color }">
          <span>{{ col.title }}</span>
          <el-tag size="small" :type="col.tag">{{ groupByStatus(col.key).length }}</el-tag>
        </div>
        <VueDraggable
          v-model="col.items"
          :animation="150"
          group="parents"
          item-key="id"
          ghost-class="ghost-card"
          class="kanban-col-body"
          @end="onDragEnd($event, col.key)"
        >
          <div
            v-for="p in col.items"
            :key="p.id"
            class="kanban-card"
            @click="goDetail(p.id)"
          >
            <div class="kanban-card-title">{{ p.title }}</div>
            <el-progress :percentage="progress(p)" :stroke-width="4" :show-text="false" style="margin: 6px 0" />
            <div class="kanban-card-meta">
              <span>📋 {{ p.workflow_tasks?.length || 0 }} 项</span>
              <span>{{ formatTime(p.updated_at) }}</span>
            </div>
          </div>
        </VueDraggable>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { VueDraggable } from 'vue-draggable-plus'
import { api } from '@/api'

const router = useRouter()
const parents = ref([])

const columns = ref([
  { key: 'draft', title: '草稿', color: '#909399', tag: 'info', items: [] },
  { key: 'confirmed', title: '已确认', color: '#e6a23c', tag: 'warning', items: [] },
  { key: 'in_progress', title: '进行中', color: '#1890ff', tag: 'primary', items: [] },
  { key: 'completed', title: '已完成', color: '#52c41a', tag: 'success', items: [] },
  { key: 'failed', title: '失败', color: '#f5222d', tag: 'danger', items: [] },
])

const colsByKey = computed(() => {
  const m = {}
  for (const c of columns.value) m[c.key] = c
  return m
})

function groupByStatus(key) {
  return colsByKey.value[key].items
}

function progress(p) {
  if (!p.workflow_tasks?.length) return 0
  const total = p.workflow_tasks.reduce((s, w) => s + (w.progress || 0), 0)
  return Math.round(total / p.workflow_tasks.length)
}

function formatTime(s) {
  if (!s) return ''
  try {
    return new Date(s).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false })
  } catch { return s }
}

async function refresh() {
  try {
    parents.value = await api('/parents')
    distribute()
  } catch (e) { /* ignore */ }
}

function distribute() {
  for (const c of columns.value) c.items = []
  for (const p of parents.value) {
    const col = colsByKey.value[p.status]
    if (col) col.items.push(p)
  }
}

function goDetail(id) {
  router.push({ name: 'Detail', params: { id } })
  localStorage.setItem('activeParentId', id)
}

async function onDragEnd(evt, targetCol) {
  // 找出被拖动的项
  const itemEl = evt.item
  const id = itemEl.getAttribute('data-id')
  // 找到该 card 所属的 parent 对象
  let dragged = null
  let sourceCol = null
  for (const c of columns.value) {
    const found = c.items.find(p => p.id === id)
    if (found) { dragged = found; sourceCol = c; break }
  }
  if (!dragged) return
  if (sourceCol.key === targetCol) return
  const oldStatus = dragged.status
  dragged.status = targetCol
  try {
    await api(`/parents/${dragged.id}/status`, {
      method: 'PATCH',
      body: { status: targetCol },
    })
    ElMessage.success(`已移到「${colsByKey.value[targetCol].title}」`)
  } catch (e) {
    dragged.status = oldStatus
    distribute()
    ElMessage.error('移动失败')
  }
}

// 事件总线：监听外部刷新
function onParentsChanged() { refresh() }
function onWsEvent(e) {
  const { event } = e.detail
  if (event?.startsWith('parent.') || event?.startsWith('workflow.') || event?.startsWith('stage.')) {
    refresh()
  }
}

onMounted(() => {
  refresh()
  window.addEventListener('parents-changed', onParentsChanged)
  window.addEventListener('ws-event', onWsEvent)
})
onBeforeUnmount(() => {
  window.removeEventListener('parents-changed', onParentsChanged)
  window.removeEventListener('ws-event', onWsEvent)
})
</script>

<style lang="scss" scoped>
.board-view { height: 100%; }

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.header-actions {
  display: flex;
  gap: 8px;
}

.kanban-card { user-select: none; }
.ghost-card {
  opacity: 0.4;
  background: #1890ff !important;
  color: #fff;
}
</style>
