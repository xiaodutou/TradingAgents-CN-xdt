<template>
  <el-card shadow="never">
    <template #header><h3>ChatGPT Plus 会员额度</h3></template>
    <p>连接你的 ChatGPT 账号，授权此本地项目使用会员额度。</p>
    <el-alert title="分析会消耗共享的会员额度；可在 ChatGPT 用量设置中管理此应用的限额。" type="info" :closable="false" />
    <el-alert v-if="error" :title="error" type="error" :closable="false" style="margin-top: 16px" />
    <div class="actions">
      <el-button type="primary" :loading="busy" @click="signIn()">Continue with ChatGPT</el-button>
      <el-button @click="refresh">刷新连接状态</el-button>
      <a :href="state.usage_url" target="_blank" rel="noopener noreferrer">管理 ChatGPT 用量</a>
    </div>
    <p v-if="state.pending">等待浏览器授权。登录完成后返回此页，连接状态会自动更新。</p>
    <el-table v-if="state.profiles.length" :data="state.profiles">
      <el-table-column prop="label" label="ChatGPT 账号连接" />
      <el-table-column label="状态" width="170">
        <template #default="{ row }">
          <el-tag :type="row.sharing ? 'success' : 'info'">{{ row.sharing ? '已授权会员额度' : '尚未授权' }}</el-tag>
          <div v-if="row.id === state.active">当前使用</div>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="270">
        <template #default="{ row }">
          <el-button size="small" :disabled="busy || row.id === state.active" @click="selectAccount(row.id)">切换</el-button>
          <el-button size="small" :disabled="busy" @click="signIn(row.id)">重新授权</el-button>
          <el-button v-if="row.connected" size="small" :disabled="busy" @click="signOut(row.id)">退出</el-button>
        </template>
      </el-table-column>
    </el-table>
    <div v-if="sharing" class="model-controls">
      <p><strong>使用 ChatGPT 会员额度</strong> · 从此账号可用的模型中选择分析模型。</p>
      <el-select v-model="selectedModel" placeholder="选择会员模型" style="width: 330px">
        <el-option v-for="model in models" :key="model.slug" :value="model.slug" :label="model.display_name" />
      </el-select>
      <el-button :loading="busy" @click="loadModels">刷新模型</el-button>
      <div class="actions">
        <el-button type="primary" :disabled="!selectedModel" :loading="busy" @click="activate">用于股票分析</el-button>
        <el-button :disabled="!selectedModel" :loading="busy" @click="test">测试连接（消耗额度）</el-button>
      </div>
      <el-alert v-if="testResult" :title="testResult" type="success" :closable="false" />
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import { ApiClient } from '@/api/request'

type Profile = { id: string; label: string; connected: boolean; sharing: boolean }
const state = ref<{ profiles: Profile[]; active: string | null; error?: string; pending: boolean; usage_url: string }>({
  profiles: [], active: null, pending: false, usage_url: 'https://chatgpt.com/#settings/Usage'
})
const models = ref<{ slug: string; display_name: string }[]>([])
const selectedModel = ref('')
const busy = ref(false)
const error = ref('')
const testResult = ref('')
const sharing = computed(() => state.value.profiles.some(p => p.id === state.value.active && p.sharing))
let timer: ReturnType<typeof setInterval> | undefined
let previousConnection = ''

function errorText(e: any) {
  const detail = e?.response?.data?.detail
  return (typeof detail === 'string' ? detail : detail?.message) || e?.message || '操作失败，请重试。'
}
async function run(action: () => Promise<void>) {
  busy.value = true
  error.value = ''
  try { await action() } catch (e) { error.value = errorText(e) } finally { busy.value = false }
}
async function refresh() {
  try {
    const response = await ApiClient.get('/api/chatgpt-plan/status')
    state.value = response.data
    if (state.value.error) error.value = state.value.error
    const connection = `${state.value.active}:${sharing.value}`
    if (connection !== previousConnection) {
      previousConnection = connection
      models.value = []
      selectedModel.value = ''
      if (sharing.value) await loadModels()
    }
  } catch (e) { error.value = errorText(e) }
}
async function signIn(profileId?: string) {
  // Create the window synchronously, before the request, to avoid popup blocking.
  const popup = window.open('about:blank', '_blank')
  await run(async () => {
    const response = await ApiClient.post('/api/chatgpt-plan/sign-in', { profile_id: profileId || null })
    if (popup) { popup.opener = null; popup.location.href = response.data.authorization_url }
    else window.location.assign(response.data.authorization_url)
    await refresh()
  })
  if (error.value && popup) popup.close()
}
async function selectAccount(id: string) {
  await run(async () => { await ApiClient.post('/api/chatgpt-plan/select', { profile_id: id }); await refresh() })
}
async function signOut(id: string) {
  await run(async () => {
    const response = await ApiClient.post('/api/chatgpt-plan/sign-out', { profile_id: id })
    ElMessage.info(response.data.message)
    await refresh()
  })
}
async function loadModels() {
  await run(async () => {
    const response = await ApiClient.get('/api/chatgpt-plan/models')
    models.value = response.data
    if (!models.value.some(m => m.slug === selectedModel.value)) selectedModel.value = models.value[0]?.slug || ''
    if (!models.value.length) error.value = '此账号未返回可用模型，请检查会员额度授权。'
  })
}
async function activate() {
  await run(async () => {
    const response = await ApiClient.post('/api/chatgpt-plan/activate-model', { slug: selectedModel.value })
    ElMessage.success(response.message)
  })
}
async function test() {
  await run(async () => {
    const response = await ApiClient.post('/api/chatgpt-plan/test', { slug: selectedModel.value }, { timeout: 200000 })
    testResult.value = `${response.data.text} · ${response.message}`
  })
}
onMounted(() => { refresh(); timer = setInterval(() => { if (!busy.value) refresh() }, 5000) })
onUnmounted(() => { if (timer) clearInterval(timer) })
</script>

<style scoped>
.actions { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; margin: 20px 0; }
.model-controls { margin-top: 24px; }
</style>
