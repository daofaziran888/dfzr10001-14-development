<template>
  <section class="page" data-module="dashboard">
    <header class="page-head">
      <div>
        <h2>采集看板管理</h2>
        <p class="page-desc">维护采集看板的统计指标、展示形式与刷新策略，提供采集全局可视化。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记看板指标</button>
        <button class="btn" type="button" @click="exportRows">导出采集看板清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无采集看板数据，可先登记看板指标</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条采集看板记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <h3 class="section-title">存储清理同步（来自保留期与分区试算）</h3>
    <p class="section-desc">试算结果实时同步到采集看板：超出保留天数的分区先进入待清理，临近到期进入待确认。</p>
    <div class="board-grid">
      <div v-for="(value, label) in boardSummary" :key="label" class="board-card">
        <span class="stat-label">{{ label }}</span>
        <strong>{{ value }}</strong>
      </div>
    </div>
    <table class="data-table">
      <thead>
        <tr>
          <th>同步编号</th><th>存储编号</th><th>分区编号</th><th>采集时间</th>
          <th>清理日期</th><th>待清理条数</th><th>状态</th><th>来源试算</th><th>更新时间</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="item in boardItems" :key="String(item.id)">
          <td>{{ item['同步编号'] }}</td>
          <td>{{ item['存储编号'] }}</td>
          <td>{{ item['分区编号'] }}</td>
          <td>{{ item['采集时间'] }}</td>
          <td>{{ item['清理日期'] }}</td>
          <td>{{ item['待清理条数'] ?? 0 }}</td>
          <td><span :class="badgeClass(item['状态'])">{{ item['状态'] }}</span></td>
          <td>{{ item['试算编号'] }}</td>
          <td>{{ item['更新时间'] }}</td>
        </tr>
        <tr v-if="!boardItems.length">
          <td colspan="9" class="empty-state">暂无存储清理同步条目</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>
type BoardRow = Record<string, string | number | null>

const ENDPOINT = '/api/dashboard'
const columns = ["指标编号", "指标名称", "统计维度", "计算公式", "刷新频率", "展示类型", "数据源", "是否展示"]
const actions = ["提交校验", "启用配置", "停用配置"]
const statuses = ["草稿", "待校验", "已启用", "已停用"]
const stats = [{"label": "看板指标总数", "value": 0}, {"label": "待校验看板指标", "value": 0}, {"label": "已启用看板指标", "value": 0}]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)

const boardItems = ref<BoardRow[]>([])
const boardSummary = ref<Record<string, number>>({})

function badgeClass(status: string | number | null): string {
  const key = String(status)
  const map: Record<string, string> = {
    待清理: 'badge wait',
    待确认: 'badge confirm',
    观察中: 'badge watch',
    已清理: 'badge done',
    已撤销: 'badge revoke',
  }
  return map[key] ?? 'badge revoke'
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '看板指标登记入口尚未接入审批流'
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) {
      throw new Error('采集看板动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采集看板操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('看板指标列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采集看板列表读取失败'
  }
}

async function loadCleanupBoard() {
  try {
    const response = await request(`${ENDPOINT}/cleanup-board`)
    if (!response.ok) {
      throw new Error('存储清理同步读取失败')
    }
    const payload = await response.json()
    boardItems.value = payload.items ?? []
    boardSummary.value = payload.summary ?? {}
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '存储清理同步读取失败'
  }
}

onMounted(() => {
  void reload()
  void loadCleanupBoard()
})
</script>
