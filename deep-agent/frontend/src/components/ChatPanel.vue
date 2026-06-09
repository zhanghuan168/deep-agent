<template>
  <transition name="slide-fade">
    <div v-show="visible" class="chat-panel">
      <div class="chat-header">
        <span><el-icon><ChatDotRound /></el-icon> 用户 ↔ Agent</span>
        <div>
          <el-button text size="small" @click="newConv">
            <el-icon><Plus /></el-icon>
          </el-button>
          <el-button text size="small" @click="$emit('update:visible', false)">
            <el-icon><Close /></el-icon>
          </el-button>
        </div>
      </div>

      <div class="chat-messages" ref="msgList">
        <el-empty v-if="!messages.length" :image-size="80" description="还没有对话，下达第一条指令吧" />
        <template v-for="(group, gi) in groupedMessages" :key="gi">
          <div class="chat-msg-time">{{ group.tsLabel }}</div>
          <div
            v-for="(m, mi) in group.items"
            :key="mi"
            :class="['chat-msg', m.role === 'boss' ? 'chat-msg-boss' : '']"
          >
            <el-avatar :size="32" class="chat-msg-avatar" :style="m.role==='boss' ? 'background:#1890ff' : 'background:#52c41a'">
              {{ m.role === 'boss' ? 'U' : 'AI' }}
            </el-avatar>
            <div style="max-width:75%">
              <el-tag v-if="m.tool" size="small" type="info" effect="plain" style="margin-bottom:2px">
                🔧 {{ m.tool }}
              </el-tag>
              <div :class="['chat-msg-bubble', m.role === 'boss' ? 'boss' : 'pm']">
                {{ m.content }}
              </div>
            </div>
          </div>
        </template>
      </div>

      <div class="chat-input-area">
        <el-input
          v-model="input"
          type="textarea"
          :rows="2"
          placeholder="下达需求：例如「做一个会记账的微信小程序」，或问「你能做什么？」"
          @keydown.enter.exact.prevent="onSend"
          :disabled="loading"
        />
        <div style="display:flex;justify-content:space-between;align-items:center">
          <el-button text size="small" @click="onSend" :loading="loading" type="primary">
            发送 Enter
          </el-button>
          <el-button v-if="canConfirmCreate" size="small" type="primary" @click="$emit('confirm-create')">
            ✓ 确认创建
          </el-button>
          <el-button v-if="canConfirmStart" size="small" type="success" @click="$emit('confirm-start')">
            🚀 确认开工
          </el-button>
        </div>
      </div>
    </div>
  </transition>
  <transition name="slide-fade">
    <el-button
      v-if="!visible"
      class="chat-toggle"
      type="primary"
      circle
      @click="$emit('update:visible', true)"
    >
      <el-icon><ChatDotRound /></el-icon>
    </el-button>
  </transition>
</template>

<script setup>
import { ref, computed, nextTick, watch, onMounted } from 'vue'

const props = defineProps({
  visible: Boolean,
  parentId: String,
  messages: { type: Array, default: () => [] },
  loading: Boolean,
  lastIntent: { type: String, default: 'chat' },
})
const emit = defineEmits(['update:visible', 'send', 'confirm-create', 'confirm-start'])

const input = ref('')
const msgList = ref(null)

// 上一条 PM 消息的 intent，决定显示哪个按钮
const canConfirmCreate = computed(() => props.lastIntent === 'ask_to_create')
const canConfirmStart = computed(() => props.lastIntent === 'ask_to_start')

function onSend() {
  const text = input.value.trim()
  if (!text || props.loading) return
  emit('send', text)
  input.value = ''
}

function newConv() {
  if (confirm('开始新对话？当前对话历史会保留。')) {
    localStorage.removeItem('activeParentId')
    location.reload()
  }
}

// 消息按时间分组（每 5 分钟一组）
const groupedMessages = computed(() => {
  const groups = []
  let current = null
  for (const m of props.messages) {
    const ts = m.ts || Date.now()
    const tsLabel = formatTime(ts)
    if (!current || Math.abs(current.lastTs - ts) > 5 * 60 * 1000) {
      current = { tsLabel, lastTs: ts, items: [] }
      groups.push(current)
    }
    current.items.push(m)
    current.lastTs = ts
  }
  return groups
})

function formatTime(ts) {
  const d = new Date(ts)
  return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false })
}

watch(() => props.messages.length, () => {
  nextTick(() => {
    if (msgList.value) msgList.value.scrollTop = msgList.value.scrollHeight
  })
})

onMounted(() => {
  nextTick(() => {
    if (msgList.value) msgList.value.scrollTop = msgList.value.scrollHeight
  })
})
</script>

<style lang="scss" scoped>
.chat-panel {
  position: fixed;
  right: 0;
  top: 56px;
  bottom: 0;
  width: 360px;
  background: #fff;
  border-left: 1px solid #ebeef5;
  z-index: 1000;
  display: flex;
  flex-direction: column;
  box-shadow: -2px 0 8px rgba(0, 0, 0, 0.06);
}

.chat-toggle {
  position: fixed !important;
  right: 20px;
  bottom: 20px;
  z-index: 999;
}

.slide-fade-enter-active, .slide-fade-leave-active {
  transition: transform 0.2s, opacity 0.2s;
}
.slide-fade-enter-from, .slide-fade-leave-to {
  transform: translateX(20px);
  opacity: 0;
}
</style>
