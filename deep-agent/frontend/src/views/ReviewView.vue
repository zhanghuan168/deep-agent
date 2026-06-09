<template>
  <div class="review-view">
    <h2 class="page-title">🎯 评审中心</h2>
    <p class="page-subtitle">查看需要人工决策的阶段（设计评审 / 代码评审 / 测试评审 / 需求确认）</p>

    <div v-for="parent in pendingParents" :key="parent.id" class="parent-block">
      <h3>
        <el-icon><Document /></el-icon>
        {{ parent.title }}
        <el-button text size="small" @click="$router.push({ name: 'Detail', params: { id: parent.id } })">
          详情
        </el-button>
      </h3>
      <el-table :data="pendingStages(parent)" border size="small" stripe>
        <el-table-column label="阶段" width="140">
          <template #default="{ row }">
            <el-tag :type="stageTag(row.status)" effect="dark">{{ stageLabel(row.name) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="工作项" prop="workflow_title" min-width="200" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <span :style="{ color: row.status === 'needs_review' ? '#e6a23c' : '' }">
              {{ statusLabel(row.status) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="评审意见" min-width="200">
          <template #default="{ row }">
            <span v-if="row.review_comment">{{ row.review_comment }}</span>
            <span v-else style="color:#909399">(无意见)</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="260" fixed="right">
          <template #default="{ row }">
            <template v-if="row.status === 'needs_review'">
              <el-button type="warning" size="small" @click="openRequirementConfirm(row)">
                确认需求清单
              </el-button>
            </template>
            <template v-else-if="row.status === 'failed'">
              <el-button type="success" size="small" @click="decide(row, 'approve')">通过</el-button>
              <el-button type="danger" size="small" @click="decide(row, 'reject')">打回</el-button>
            </template>
          </template>
        </el-table-column>
      </el-table>
    </div>
    <el-empty v-if="!pendingParents.length" description="没有待评审的阶段" />

    <!-- 需求清单确认对话框 -->
    <el-dialog v-model="reqDialogVisible" title="确认需求清单" width="800px" :close-on-click-modal="false">
      <div v-if="reqStage">
        <p style="margin-bottom:12px;color:#666">
          工作项：<strong>{{ reqStage.workflow_title }}</strong>
        </p>
        <p style="margin-bottom:16px;color:#888;font-size:13px">
          请编辑需求清单，确认后系统将进入技术设计阶段。评审意见：{{ reqStage.review_comment || '(无)' }}
        </p>

        <el-table :data="reqItems" border size="small" style="margin-bottom:12px">
          <el-table-column label="优先级" width="90">
            <template #default="{ row, $index }">
              <el-select v-model="row.priority" size="small" style="width:80px">
                <el-option label="high" value="high" />
                <el-option label="medium" value="medium" />
                <el-option label="low" value="low" />
              </el-select>
            </template>
          </el-table-column>
          <el-table-column label="需求内容" min-width="200">
            <template #default="{ row }">
              <el-input v-model="row.content" type="textarea" :rows="2" size="small" />
            </template>
          </el-table-column>
          <el-table-column label="验收标准" min-width="200">
            <template #default="{ row }">
              <el-input v-model="row.acceptance_criteria_text" type="textarea" :rows="2" size="small" placeholder="多行，每行一个验收点" />
            </template>
          </el-table-column>
          <el-table-column label="依赖" width="100">
            <template #default="{ row }">
              <el-input v-model="row.dependencies_text" size="small" placeholder="req-1,req-2" />
            </template>
          </el-table-column>
         <el-table-column label="操作" width="60">
            <template #default="{ $index }">
              <el-button type="danger" size="small" link @click="reqItems.splice($index, 1)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>

        <el-button size="small" @click="addReqItem">+ 添加需求</el-button>
      </div>
      <template #footer>
        <el-button @click="reqDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="confirmRequirement">确认需求清单</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Document } from '@element-plus/icons-vue'
import { api } from '@/api'

const parents = ref([])
const reqDialogVisible = ref(false)
const reqStage = ref(null)
const reqItems = ref([])

const pendingParents = computed(() => {
  return parents.value.filter(p =>
    (p.workflow_tasks || []).some(w =>
      (w.stages || []).some(s => s.status === 'failed' || s.status === 'needs_review')
    )
  )
})

function pendingStages(parent) {
  const rows = []
  for (const w of (parent.workflow_tasks || [])) {
    for (const s of (w.stages || [])) {
      if (s.status === 'failed' || s.status === 'needs_review') {
        rows.push({ ...s, workflow_title: w.title })
      }
    }
  }
  return rows
}

async function refresh() {
  try { parents.value = await api('/parents') } catch (e) { /* ignore */ }
}

async function openRequirementConfirm(stage) {
  reqStage.value = stage
  reqDialogVisible.value = true
  // 加载需求清单
  try {
    const data = await api(`/stages/${stage.id}/requirement-items`)
    reqItems.value = (data.items || []).map(item => ({
      ...item,
      acceptance_criteria_text: Array.isArray(item.acceptance_criteria)
        ? item.acceptance_criteria.join('\n')
        : (item.acceptance_criteria || ''),
      dependencies_text: Array.isArray(item.dependencies)
        ? item.dependencies.join(',')
        : (item.dependencies || ''),
    }))
  } catch (e) {
    reqItems.value = []
  }
}

function addReqItem() {
  reqItems.value.push({
    id: `req-${Date.now()}`,
    content: '',
    priority: 'medium',
    acceptance_criteria_text: '',
    dependencies_text: '',
  })
}

async function confirmRequirement() {
  if (!reqStage.value) return
  // 序列化验收标准和依赖
  const items = reqItems.value.map((item, idx) => ({
    id: item.id || `req-${idx + 1}`,
    content: item.content,
    priority: item.priority,
    acceptance_criteria: item.acceptance_criteria_text.split('\n').map(s => s.trim()).filter(Boolean),
    dependencies: item.dependencies_text.split(',').map(s => s.trim()).filter(Boolean),
  }))

  try {
    await api(`/stages/${reqStage.value.id}/confirm-requirement`, {
      method: 'POST',
      body: { items },
    })
    ElMessage.success('需求清单已确认')
    reqDialogVisible.value = false
    refresh()
  } catch (e) {
    ElMessage.error('确认失败: ' + e.message)
  }
}

async function decide(stage, decision) {
  const label = decision === 'approve' ? '通过' : '打回'
  try {
    const { value: comment } = await ElMessageBox.prompt(
      `${label} 评审（可附说明）`,
      '人工评审',
      { confirmButtonText: '确认', cancelButtonText: '取消' }
    )
    await api(`/stages/${stage.id}/review`, {
      method: 'POST',
      body: { decision, comment: comment || label },
    })
    ElMessage.success(`已${label}`)
    refresh()
  } catch (e) { /* 用户取消或失败 */ }
}

const stageLabel = n => ({
  requirement_analysis: '需求分析',
  requirement_review: '需求评审',
  technical_design: '技术设计',
  technical_review: '技术评审',
  task_breakdown: '任务拆解',
  implementation: '编码实现',
  code_review: '代码审查',
  testing: '功能测试',
}[n] || n)

const stageTag = s => ({ pending: 'info', running: 'primary', succeeded: 'success', failed: 'danger', needs_review: 'warning' }[s] || '')

const statusLabel = s => ({ needs_review: '待确认', failed: '被打回' }[s] || s)

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
.parent-block {
  background: #fff;
  border-radius: 4px;
  padding: 16px;
  margin-bottom: 16px;
  box-shadow: 0 1px 4px rgba(0, 21, 41, 0.08);
}
.parent-block h3 {
  margin: 0 0 12px;
  font-size: 15px;
  display: flex;
  align-items: center;
  gap: 8px;
}
</style>
