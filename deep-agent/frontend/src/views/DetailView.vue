<template>
  <div v-if="parent" class="detail-view">
    <el-page-header @back="$router.push('/board')" :content="parent.title" />

    <el-descriptions :column="3" border style="margin: 16px 0">
      <el-descriptions-item label="状态">
        <el-tag :type="statusTag(parent.status)">{{ statusLabel(parent.status) }}</el-tag>
      </el-descriptions-item>
      <el-descriptions-item label="工作项数">{{ parent.workflow_tasks?.length || 0 }}</el-descriptions-item>
      <el-descriptions-item label="创建时间">{{ formatTime(parent.created_at) }}</el-descriptions-item>
      <el-descriptions-item label="操作" :span="3">
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <el-button v-if="parent.status === 'draft' || parent.status === 'confirmed'" size="small" type="primary" @click="doAction('start')" :loading="actionLoading">启动</el-button>
          <el-button v-if="parent.status === 'in_progress'" size="small" type="warning" @click="doAction('pause')" :loading="actionLoading">暂停</el-button>
          <el-button v-if="parent.status === 'blocked'" size="small" type="success" @click="doAction('resume')" :loading="actionLoading">继续</el-button>
          <el-button v-if="parent.status === 'in_progress' || parent.status === 'blocked' || parent.status === 'confirmed'" size="small" type="danger" @click="doAction('stop')" :loading="actionLoading">停止</el-button>
          <el-button v-if="parent.status === 'failed' || parent.status === 'completed'" size="small" type="info" @click="deleteTask" :loading="actionLoading">删除</el-button>
        </div>
      </el-descriptions-item>
      <el-descriptions-item label="描述" :span="3">
        <pre class="desc-pre">{{ parent.description || '(无)' }}</pre>
      </el-descriptions-item>
    </el-descriptions>

    <h3 style="margin: 24px 0 12px">📋 工作项</h3>
    <el-table :data="parent.workflow_tasks || []" border size="small" stripe>
      <el-table-column prop="title" label="标题" min-width="200" />
      <el-table-column label="状态" width="120">
        <template #default="{ row }">
          <el-tag :type="wfStatusTag(row.status)" size="small">{{ wfStatusLabel(row.status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="进度" width="180">
        <template #default="{ row }">
          <el-progress :percentage="row.progress || 0" :stroke-width="8" />
        </template>
      </el-table-column>
      <el-table-column label="阶段" min-width="320">
        <template #default="{ row }">
          <el-tag
            v-for="s in row.stages"
            :key="s.id"
            :type="stageTag(s.status)"
            size="small"
            effect="plain"
            class="stage-pill"
          >
            {{ stageLabel(s.name) }}
            <el-icon v-if="s.status === 'succeeded'"><Check /></el-icon>
            <el-icon v-else-if="s.status === 'failed'"><Close /></el-icon>
            <el-icon v-else-if="s.status === 'running'"><Loading /></el-icon>
          </el-tag>
        </template>
      </el-table-column>
    </el-table>

    <h3 style="margin: 24px 0 12px">📅 甘特图</h3>
    <div ref="ganttHost" class="gantt-host"></div>

    <h3 style="margin: 24px 0 12px">📝 实时日志</h3>
    <div class="log-pane" ref="logList">
      <div v-for="l in logs" :key="l.id" :class="['log-line', `log-${l.level}`]">
        <span class="log-time">{{ formatTime(l.created_at) }}</span>
        <span class="log-level">{{ l.level }}</span>
        <span class="log-msg">{{ l.message }}</span>
      </div>
    </div>

    <DocumentPanel v-if="parent" :parent-id="parent.id" />
  </div>
  <el-empty v-else description="请在看板选择一个任务" />
</template>

<script setup>
import { ref, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '@/api'
import { ElMessage } from 'element-plus'
import { DocumentPanel } from '@/components'

const route = useRoute()
const parent = ref(null)
const logs = ref([])
const ganttHost = ref(null)
const logList = ref(null)
const actionLoading = ref(false)
let ganttInstance = null

async function refresh() {
  const id = route.params.id || localStorage.getItem('activeParentId')
  if (!id) return
  try {
    parent.value = await api(`/parents/${id}`)
    logs.value = await api(`/logs?parent_id=${id}&limit=200`)
    await nextTick()
    renderGantt()
    scrollLog()
  } catch (e) { /* ignore */ }
}

function buildGanttTasks() {
  if (!parent.value) return []
  const tasks = []
  const created = parent.value.created_at ? new Date(parent.value.created_at) : new Date()
  tasks.push({
    id: parent.value.id,
    name: parent.value.title,
    start: created.toISOString().slice(0, 10),
    end: new Date().toISOString().slice(0, 10),
    progress: 0,
    dependencies: '',
  })
  for (const w of (parent.value.workflow_tasks || [])) {
    const wStart = w.started_at || w.created_at || new Date().toISOString()
    const wEnd = w.finished_at || new Date().toISOString()
    tasks.push({
      id: w.id,
      name: '  └ ' + w.title,
      start: wStart.slice(0, 10),
      end: wEnd.slice(0, 10),
      progress: w.progress || 0,
      dependencies: parent.value.id,
    })
  }
  return tasks
}

function renderGantt() {
  const host = ganttHost.value
  if (!host || !window.Gantt) return
  host.innerHTML = ''
  const tasks = buildGanttTasks()
  if (tasks.length === 0) return
  try {
    ganttInstance = new window.Gantt(host, tasks, {
      view_mode: 'Day',
      bar_height: 22,
      padding: 18,
      readonly: true,
    })
  } catch (e) { console.warn('gantt render fail', e) }
}

function scrollLog() {
  nextTick(() => {
    if (logList.value) logList.value.scrollTop = logList.value.scrollHeight
  })
}

const statusLabel = s => ({ draft: '草稿', confirmed: '已确认', scheduled: '已排期', in_progress: '进行中', completed: '已完成', failed: '失败' }[s] || s)
const statusTag = s => ({ draft: 'info', confirmed: 'warning', in_progress: 'primary', completed: 'success', failed: 'danger' }[s] || '')
const wfStatusLabel = s => ({ created: '已创建', in_progress: '执行中', reviewing: '评审中', completed: '已完成', failed: '失败' }[s] || s)
const wfStatusTag = s => ({ created: 'info', in_progress: 'primary', reviewing: 'warning', completed: 'success', failed: 'danger' }[s] || '')
const stageLabel = n => ({ requirement: '需求', design: '设计', design_review: '设计评审', development: '开发', code_review: '代码评审', testing: '测试', test_review: '测试评审' }[n] || n)
const stageTag = s => ({ pending: 'info', running: 'primary', succeeded: 'success', failed: 'danger', skipped: 'info', needs_review: 'warning' }[s] || '')

function formatTime(s) {
  if (!s) return ''
  try { return new Date(s).toLocaleString('zh-CN', { hour12: false }) } catch { return s }
}

async function doAction(action) {
  if (!parent.value) return
  actionLoading.value = true
  try {
    await api(`/parents/${parent.value.id}/action`, {
      method: 'POST',
      body: { action },
    })
    ElMessage.success(action === 'start' ? '已启动' : action === 'stop' ? '已停止' : action === 'pause' ? '已暂停' : action === 'resume' ? '已继续' : '操作成功')
    await refresh()
    window.dispatchEvent(new CustomEvent('parents-changed'))
  } catch (e) {
    ElMessage.error('操作失败：' + e.message)
  } finally {
    actionLoading.value = false
  }
}

async function deleteTask() {
  if (!parent.value) return
  if (!confirm('确定删除该任务？')) return
  actionLoading.value = true
  try {
    await api(`/parents/${parent.value.id}`, { method: 'DELETE' })
    ElMessage.success('已删除')
    window.dispatchEvent(new CustomEvent('parents-changed'))
    history.back()
  } catch (e) {
    ElMessage.error('删除失败：' + e.message)
  } finally {
    actionLoading.value = false
  }
}

function onWsEvent(e) {
  const { event } = e.detail
  if (event?.startsWith('parent.') || event?.startsWith('workflow.') || event?.startsWith('stage.')) {
    refresh()
  }
}

onMounted(() => {
  refresh()
  window.addEventListener('ws-event', onWsEvent)
  window.addEventListener('parents-changed', refresh)
})
onBeforeUnmount(() => {
  window.removeEventListener('ws-event', onWsEvent)
  window.removeEventListener('parents-changed', refresh)
})
</script>

<style scoped>
.desc-pre { margin: 0; white-space: pre-wrap; font-family: inherit; font-size: 13px; }
</style>
