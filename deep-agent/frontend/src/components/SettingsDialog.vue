<template>
  <el-dialog v-model="visible" title="运行时配置" width="640px" :close-on-click-modal="false">
    <el-alert
      v-if="!form.provider"
      title="尚未配置 LLM，项目经理会用规则回退模式（仍能跑，但智能度有限）"
      type="info"
      :closable="false"
      show-icon
      style="margin-bottom: 16px"
    />
    <el-form label-position="top">
      <el-form-item label="厂商">
        <el-select v-model="form.provider" filterable @change="onProviderChange" style="width:100%">
          <el-option-group label="国内（推荐）">
            <el-option v-for="p in presets.filter(x => x.region==='cn')" :key="p.id" :label="p.label" :value="p.id" />
          </el-option-group>
          <el-option-group label="海外">
            <el-option v-for="p in presets.filter(x => x.region==='global')" :key="p.id" :label="p.label" :value="p.id" />
          </el-option-group>
          <el-option-group label="本地">
            <el-option v-for="p in presets.filter(x => x.region==='local')" :key="p.id" :label="p.label" :value="p.id" />
          </el-option-group>
          <el-option label="自定义" value="custom" />
        </el-select>
      </el-form-item>
      <el-form-item label="Base URL">
        <el-input v-model="form.base_url" placeholder="https://api.example.com/v1" />
      </el-form-item>
      <el-form-item label="Model">
        <el-input v-model="form.model" placeholder="qwen2.5:7b / glm-4-flash / deepseek-chat ..." />
      </el-form-item>
      <el-form-item label="API Key">
        <el-input v-model="form.api_key" type="password" show-password placeholder="sk-..." />
        <div class="form-hint" v-if="form.provider === 'ollama'">Ollama 本地模式无需 key</div>
      </el-form-item>
      <el-form-item label="Temperature">
        <el-input-number v-model="form.temperature" :min="0" :max="2" :step="0.1" />
        <span class="form-hint">越高越发散，0.2 适合做规划</span>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button :loading="testing" @click="test">测试连接</el-button>
      <el-button type="primary" :loading="saving" @click="save">保存</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, watch, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '@/api'

const props = defineProps({ modelValue: Boolean })
const emit = defineEmits(['update:modelValue', 'saved'])

const visible = ref(props.modelValue)
watch(() => props.modelValue, (v) => visible.value = v)
watch(visible, (v) => emit('update:modelValue', v))

const presets = [
  { id: 'deepseek', label: 'Deepseek（深度求索）', region: 'cn', base_url: 'https://api.deepseek.com/v1', model: 'deepseek-chat' },
  { id: 'MiniMax', label: 'MiniMax',  region: 'cn', base_url: 'https://api.minimaxi.com/v1', model: 'MiniMax-M3' },
  { id: 'qwen', label: '通义千问（阿里 DashScope）', region: 'cn', base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1', model: 'qwen-plus' },
  { id: 'glm', label: '智谱 GLM', region: 'cn', base_url: 'https://open.bigmodel.cn/api/paas/v4/', model: 'glm-4-flash' },
  { id: 'moonshot', label: 'Moonshot Kimi', region: 'cn', base_url: 'https://api.moonshot.cn/v1', model: 'moonshot-v1-8k' },
  { id: 'doubao', label: '豆包（字节火山）', region: 'cn', base_url: 'https://ark.cn-beijing.volces.com/api/v3', model: 'doubao-pro-32k' },
  { id: 'qianfan', label: '百度千帆', region: 'cn', base_url: 'https://qianfan.baidubce.com/v2', model: 'ernie-3.5-8k' },
  { id: 'hunyuan', label: '腾讯混元', region: 'cn', base_url: 'https://api.hunyuan.cloud.tencent.com/v1', model: 'hunyuan-standard' },
  { id: 'MiniMax', label: 'MiniMax', region: 'cn', base_url: 'https://api.minimaxi.com/v1', model: 'MiniMax-M3' },
  { id: 'openai', label: 'OpenAI', region: 'global', base_url: 'https://api.openai.com/v1', model: 'gpt-4o-mini' },
  { id: 'anthropic', label: 'Anthropic Claude', region: 'global', base_url: '', model: 'claude-3-5-sonnet-20241022' },
  { id: 'ollama', label: 'Ollama（本地）', region: 'local', base_url: 'http://127.0.0.1:11434/v1', model: 'qwen2.5:7b' },
]

const form = ref({ provider: 'ollama', base_url: 'http://127.0.0.1:11434/v1', model: 'qwen2.5:7b', api_key: '', temperature: 0.2 })
const saving = ref(false)
const testing = ref(false)

async function load() {
  try {
    const r = await api('/settings')
    const s = r.settings || {}
    form.value = {
      provider: s['llm.provider'] || 'ollama',
      base_url: s['llm.base_url'] || 'http://127.0.0.1:11434/v1',
      model: s['llm.model'] || 'qwen2.5:7b',
      api_key: '',
      temperature: parseFloat(s['llm.temperature'] || '0.2'),
    }
  } catch (e) { /* ignore */ }
}

function onProviderChange(pid) {
  const p = presets.find(x => x.id === pid)
  if (p) {
    form.value.base_url = p.base_url
    form.value.model = p.model
  }
}

async function save() {
  saving.value = true
  try {
    await api('/settings', {
      method: 'PUT',
      body: { settings: {
        'llm.provider': form.value.provider,
        'llm.base_url': form.value.base_url,
        'llm.model': form.value.model,
        'llm.api_key': form.value.api_key || '',
        'llm.temperature': String(form.value.temperature),
      }},
    })
    ElMessage.success('配置已保存')
    emit('saved')
    visible.value = false
  } finally {
    saving.value = false
  }
}

async function test() {
  testing.value = true
  try {
    const r = await api('/settings/test', {
      method: 'POST',
      body: { override: {
        'llm.provider': form.value.provider,
        'llm.base_url': form.value.base_url,
        'llm.model': form.value.model,
        'llm.api_key': form.value.api_key || '',
        'llm.temperature': String(form.value.temperature),
      }},
    })
    if (r.ok) ElMessage.success('连接成功！')
    else ElMessage.error('调用失败：' + (r.error || '未知错误'))
  } finally {
    testing.value = false
  }
}

watch(visible, (v) => { if (v) load() })
</script>

<style scoped>
.form-hint {
  font-size: 12px;
  color: #909399;
  margin-left: 8px;
}
</style>
