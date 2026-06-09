<template>
  <el-container class="layout">
    <!-- 侧边栏 -->
    <el-aside :width="sidebarWidth" class="sidebar">
      <div class="sidebar-logo">
        <span class="logo-icon">⚡</span>
        <transition name="fade">
          <span v-show="!sidebarCollapsed" class="logo-text">D-Agent</span>
        </transition>
      </div>
      <el-menu
        :default-active="activeMenu"
        :collapse="sidebarCollapsed"
        background-color="#001529"
        text-color="rgba(255,255,255,0.85)"
        active-text-color="#1890ff"
        router
      >
        <el-menu-item index="/board">
          <el-icon><Grid /></el-icon>
          <template #title>任务看板</template>
        </el-menu-item>
        <el-menu-item index="/detail" :disabled="!activeParentId">
          <el-icon><Document /></el-icon>
          <template #title>任务详情</template>
        </el-menu-item>
        <el-menu-item index="/gantt">
          <el-icon><DataLine /></el-icon>
          <template #title>甘特图</template>
        </el-menu-item>
        <el-menu-item index="/review">
          <el-icon><Tickets /></el-icon>
          <template #title>评审中心</template>
        </el-menu-item>
      </el-menu>
    </el-aside>

    <el-container>
      <!-- 顶栏 -->
      <el-header class="topbar">
        <div class="topbar-left">
          <el-button text size="small" @click="toggleSidebar">
            <el-icon><Expand v-if="sidebarCollapsed" /><Fold v-else /></el-icon>
          </el-button>
          <el-breadcrumb separator="/" class="breadcrumb">
            <el-breadcrumb-item :to="{ path: '/board' }">首页</el-breadcrumb-item>
            <el-breadcrumb-item>{{ currentPageTitle }}</el-breadcrumb-item>
          </el-breadcrumb>
        </div>
        <div class="topbar-right">
          <el-button text size="small" @click="settingsOpen = true" :type="llmConfigured ? 'success' : 'warning'">
            <el-icon><Setting /></el-icon>
            LLM: {{ llmConfigured ? llmSummary : '未配置' }}
          </el-button>
          <el-tag :type="wsConnected ? 'success' : 'danger'" effect="dark" size="small">
            WS: {{ wsConnected ? '已连接' : '断开' }}
          </el-tag>
          <el-dropdown @command="onUserCmd">
            <span class="user-avatar">
              <el-avatar :size="32" style="background:#1890ff">U</el-avatar>
              <span class="user-name">用户</span>
              <el-icon><ArrowDown /></el-icon>
            </span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="settings">⚙️ 系统设置</el-dropdown-item>
                <el-dropdown-item command="reset" divided>🔄 重置数据库</el-dropdown-item>
                <el-dropdown-item command="docs">📖 查看文档</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>

      <!-- 主内容 -->
      <el-main class="main-content">
        <router-view />
      </el-main>
    </el-container>

    <!-- 聊天侧栏（可折叠） -->
    <ChatPanel
      v-model:visible="chatVisible"
      :parent-id="activeParentId"
      :messages="messages"
      :loading="chatLoading"
      :last-intent="lastIntent"
      @send="sendChat"
      @confirm-create="confirmCreateTask"
      @confirm-start="confirmStartTask"
    />

    <!-- 设置对话框 -->
    <SettingsDialog v-model="settingsOpen" />

    <!-- 重置数据库确认 -->
    <el-dialog v-model="resetDialogVisible" title="重置数据库" width="400px">
      <p>确定要删除所有任务数据并重启吗？</p>
      <p style="color:#909399;font-size:12px">⚠️ 这是不可恢复操作。LLM 配置也会被清空。</p>
      <template #footer>
        <el-button @click="resetDialogVisible = false">取消</el-button>
        <el-button type="danger" @click="doReset">确认重置</el-button>
      </template>
    </el-dialog>
  </el-container>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '@/api'
import ChatPanel from '@/components/ChatPanel.vue'
import SettingsDialog from '@/components/SettingsDialog.vue'

const route = useRoute()
const router = useRouter()

// 侧边栏
const sidebarCollapsed = ref(false)
const sidebarWidth = computed(() => sidebarCollapsed.value ? '64px' : '210px')
const toggleSidebar = () => { sidebarCollapsed.value = !sidebarCollapsed.value }

// 当前页标题
const currentPageTitle = computed(() => {
  const map = { '/board': '任务看板', '/detail': '任务详情', '/gantt': '甘特图', '/review': '评审中心' }
  return map[route.path] || '首页'
})
const activeMenu = computed(() => route.path)

// WebSocket
const wsConnected = ref(false)
let ws = null
let wsReconnectTimer = null
function connectWS() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  ws = new WebSocket(`${proto}://${location.host}/api/ws`)
  ws.onopen = () => { wsConnected.value = true }
  ws.onclose = () => {
    wsConnected.value = false
    wsReconnectTimer = setTimeout(connectWS, 2000)
  }
  ws.onerror = () => { if (ws) ws.close() }
  ws.onmessage = (ev) => {
    try {
      const { event, data } = JSON.parse(ev.data)
      window.dispatchEvent(new CustomEvent('ws-event', { detail: { event, data } }))
    } catch (e) { /* ignore */ }
  }
}

// 当前激活的父任务 ID（详情页路由用）
const activeParentId = ref(localStorage.getItem('activeParentId') || null)

// 聊天
const chatVisible = ref(true)
const messages = ref([])
const chatLoading = ref(false)
const lastIntent = ref('plan')

async function sendChat(text) {
  chatLoading.value = true
  messages.value.push({ role: 'boss', content: text, ts: Date.now() })
  try {
    const r = await api('/chat', { method: 'POST', body: { message: text, parent_id: activeParentId.value } })
    activeParentId.value = r.parent_id || activeParentId.value
    if (activeParentId.value) localStorage.setItem('activeParentId', activeParentId.value)
    lastIntent.value = r.intent || 'plan'
    const msg = {
      role: 'project_manager',
      content: r.content,
      plan: r.data?.plan,
      intent: r.intent || 'plan',
      ts: Date.now(),
    }
    messages.value.push(msg)
    if (r.intent === 'plan') {
      // 只在 plan 模式提示
      ElMessage.success('已发送')
      window.dispatchEvent(new CustomEvent('parents-changed'))
    }
  } catch (e) {
    messages.value.push({ role: 'project_manager', content: '请求失败：' + e.message, intent: 'chat', ts: Date.now() })
  } finally {
    chatLoading.value = false
  }
}

async function confirmCreateTask() {
  if (!activeParentId.value) return
  chatLoading.value = true
  try {
    const r = await api('/chat/confirm-create', {
      method: 'POST',
      body: { parent_id: activeParentId.value },
    })
    activeParentId.value = r.parent_id || activeParentId.value
    lastIntent.value = r.intent || 'ask_to_start'
    messages.value.push({ role: 'project_manager', content: r.content, intent: r.intent, plan: r.data?.plan, ts: Date.now() })
    ElMessage.success('已创建任务')
    window.dispatchEvent(new CustomEvent('parents-changed'))
  } catch (e) {
    ElMessage.error('创建失败：' + e.message)
  } finally {
    chatLoading.value = false
  }
}

async function confirmStartTask() {
  if (!activeParentId.value) return
  chatLoading.value = true
  try {
    const r = await api('/chat/confirm-start', {
      method: 'POST',
      body: { parent_id: activeParentId.value },
    })
    activeParentId.value = r.parent_id || activeParentId.value
    lastIntent.value = r.intent || 'chat'
    messages.value.push({ role: 'project_manager', content: r.content, intent: r.intent, ts: Date.now() })
    ElMessage.success('已开工')
    window.dispatchEvent(new CustomEvent('parents-changed'))
  } catch (e) {
    ElMessage.error('开工失败：' + e.message)
  } finally {
    chatLoading.value = false
  }
}

// 设置
const settingsOpen = ref(false)
const llmConfigured = ref(false)
const llmSummary = ref('')

async function loadSettingsBadge() {
  try {
    const r = await api('/settings')
    const s = r.settings || {}
    const provider = s['llm.provider']
    llmConfigured.value = !!provider
    if (provider === 'ollama') llmSummary.value = 'Ollama'
    else llmSummary.value = provider
  } catch (e) { /* ignore */ }
}

// 重置数据库
const resetDialogVisible = ref(false)
async function doReset() {
  try {
    // 后端没专门接口，前端先提示手动操作
    ElMessageBox.alert('请到服务器目录删除 data/dagent.db（及 -wal/-shm），然后重启服务。', '手动重置', { type: 'warning' })
  } finally {
    resetDialogVisible.value = false
  }
}

// 用户菜单
function onUserCmd(cmd) {
  if (cmd === 'settings') settingsOpen.value = true
  if (cmd === 'reset') resetDialogVisible.value = true
  if (cmd === 'docs') window.open('https://github.com/', '_blank')
}

onMounted(() => {
  connectWS()
  loadSettingsBadge()
})

onBeforeUnmount(() => {
  if (ws) ws.close()
  if (wsReconnectTimer) clearTimeout(wsReconnectTimer)
})
</script>

<style lang="scss" scoped>
.layout {
  height: 100vh;
}

.sidebar {
  background: #001529;
  transition: width 0.2s;
  overflow-x: hidden;
}

.sidebar-logo {
  height: 60px;
  display: flex;
  align-items: center;
  padding: 0 20px;
  color: #fff;
  font-size: 18px;
  font-weight: 600;
  border-bottom: 1px solid rgba(255, 255, 255, 0.05);
  background: #002140;

  .logo-icon { font-size: 24px; margin-right: 8px; }
  .logo-text { white-space: nowrap; }
}

:deep(.el-menu) {
  border-right: none;
}

.topbar {
  background: #fff;
  border-bottom: 1px solid #ebeef5;
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0 16px;
  height: 56px !important;
}

.topbar-left {
  display: flex;
  align-items: center;
  gap: 16px;
}

.topbar-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.breadcrumb {
  font-size: 14px;
}

.user-avatar {
  display: flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
  padding: 0 8px;
  border-radius: 4px;

  &:hover { background: #f5f7fa; }

  .user-name { font-size: 14px; color: #303133; }
}

.main-content {
  background: #f0f2f5;
  padding: 16px;
}

.fade-enter-active, .fade-leave-active { transition: opacity 0.2s; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
</style>
