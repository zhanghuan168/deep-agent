<template>
  <div class="gantt-view">
    <h2 class="page-title">📅 全局甘特图</h2>
    <p class="page-subtitle">展示所有父任务 + 工作项的时间线</p>

    <div class="gantt-host" ref="host"></div>
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { api } from '@/api'

const host = ref(null)
let ganttInstance = null

async function refresh() {
  try {
    const parents = await api('/parents')
    const tasks = []
    for (const p of parents) {
      const created = p.created_at ? new Date(p.created_at) : new Date()
      const end = p.finished_at || new Date()
      tasks.push({
        id: p.id,
        name: p.title,
        start: created.toISOString().slice(0, 10),
        end: end.toISOString().slice(0, 10),
        progress: 0,
        dependencies: '',
      })
      for (const w of (p.workflow_tasks || [])) {
        const wStart = w.started_at || w.created_at || new Date().toISOString()
        const wEnd = w.finished_at || new Date().toISOString()
        tasks.push({
          id: w.id,
          name: '  └ ' + w.title,
          start: wStart.slice(0, 10),
          end: wEnd.slice(0, 10),
          progress: w.progress || 0,
          dependencies: p.id,
        })
      }
    }
    await nextTick()
    if (host.value && window.Gantt && tasks.length > 0) {
      host.value.innerHTML = ''
      ganttInstance = new window.Gantt(host.value, tasks, {
        view_mode: 'Day',
        bar_height: 22,
        padding: 18,
        readonly: true,
      })
    }
  } catch (e) { /* ignore */ }
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
})
onBeforeUnmount(() => {
  window.removeEventListener('ws-event', onWsEvent)
})
</script>

<style scoped>
.gantt-host {
  background: #fff;
  border: 1px solid #ebeef5;
  border-radius: 4px;
  padding: 16px;
  min-height: 400px;
  overflow-x: auto;
}
</style>
