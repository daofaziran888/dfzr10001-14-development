<template>
  <section class="page" data-module="storage">
    <header class="page-head">
      <div>
        <h2>采集数据存储管理</h2>
        <p class="page-desc">维护采集数据的存储目标、压缩归档与保留策略，并按保留天数试算分区清理时间线。</p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="exportRows">导出采集数据存储清单</button>
      </div>
    </header>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>存储编号</span>
        <input v-model="keyword" placeholder="按存储编号检索" />
      </label>
      <label class="filter-item">
        <span>状态</span>
        <input v-model="statusFilter" placeholder="草稿/待校验/已启用/已停用" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>配置版本</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td>v{{ row['配置版本'] ?? 1 }}</td>
          <td class="row-actions">
            <button class="link" type="button" @click="runAction('提交校验', row)">提交校验</button>
            <button class="link" type="button" @click="runAction('启用配置', row)">启用配置</button>
            <button class="link" type="button" @click="runAction('停用配置', row)">停用配置</button>
            <button class="link" type="button" @click="openConfig(row)">修改配置</button>
            <button class="link" type="button" @click="openTrial(row)">保留期试算</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 2" class="empty-state">暂无采集数据存储数据</td>
        </tr>
      </tbody>
    </table>

    <h3 class="section-title">清理工作台（同步采集看板）</h3>
    <p class="section-desc">试算完成后，超出保留天数的分区进入待清理，临近到期的进入待确认；确认/取消实时同步。</p>
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
          <td colspan="9" class="empty-state">暂无同步条目，发起一次保留期试算后自动出现</td>
        </tr>
      </tbody>
    </table>

    <h3 class="section-title">历史清理记录</h3>
    <table class="data-table">
      <thead>
        <tr>
          <th>清理编号</th><th>来源试算</th><th>存储编号</th><th>分区编号</th>
          <th>采集时间</th><th>清理日期</th><th>清理条数</th><th>释放数据量(MB)</th><th>备份策略</th><th>记录时间</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="item in cleanupItems" :key="String(item.id)">
          <td>{{ item['清理编号'] }}</td>
          <td>{{ item['试算编号'] }}</td>
          <td>{{ item['存储编号'] }}</td>
          <td>{{ item['分区编号'] }}</td>
          <td>{{ item['采集时间'] }}</td>
          <td>{{ item['清理日期'] }}</td>
          <td>{{ item['清理条数'] }}</td>
          <td>{{ item['释放数据量MB'] }}</td>
          <td>{{ item['备份策略'] }}</td>
          <td>{{ item['记录时间'] }}</td>
        </tr>
        <tr v-if="!cleanupItems.length">
          <td colspan="10" class="empty-state">暂无历史清理记录，确认试算后在此留痕</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条采集数据存储记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
      <span v-if="successMessage" class="success-text">{{ successMessage }}</span>
    </footer>

    <!-- 修改存储配置 -->
    <div v-if="configModal.open" class="modal-mask" @click.self="configModal.open = false">
      <div class="modal-card">
        <div class="modal-head">
          <h3>修改存储配置 · {{ configModal.form['存储编号'] }}</h3>
          <button class="btn ghost small" type="button" @click="configModal.open = false">关闭</button>
        </div>
        <div class="form-grid">
          <label v-for="field in editableFields" :key="field">
            <span>{{ field }}</span>
            <input v-if="field !== '是否启用'" v-model="configModal.form[field]" :placeholder="`请输入${field}`" />
            <select v-else v-model="configModal.form[field]">
              <option value="是">是</option>
              <option value="否">否</option>
            </select>
          </label>
        </div>
        <p class="section-desc">保存即生成新版本配置，未确认的旧试算会自动作废；备份策略与分区顺序不会被试算改写。</p>
        <div class="modal-foot">
          <button class="btn" type="button" @click="configModal.open = false">取消</button>
          <button class="btn primary" type="button" :disabled="saving" @click="saveConfig">{{ saving ? '保存中…' : '保存新版本' }}</button>
        </div>
      </div>
    </div>

    <!-- 保留期试算 -->
    <div v-if="trialModal.open" class="modal-mask" @click.self="trialModal.open = false">
      <div class="modal-card">
        <div class="modal-head">
          <h3>保留期与分区试算 · {{ trialModal.storage?.['存储编号'] }}</h3>
          <button class="btn ghost small" type="button" @click="trialModal.open = false">关闭</button>
        </div>

        <div class="form-grid">
          <label>
            <span>存储类型</span><input :value="trialModal.storage?.['存储类型']" disabled />
          </label>
          <label>
            <span>连接串</span><input :value="trialModal.storage?.['连接串']" disabled />
          </label>
          <label>
            <span>压缩方式</span><input :value="trialModal.storage?.['压缩方式']" disabled />
          </label>
          <label>
            <span>分区策略</span><input :value="trialModal.storage?.['分区策略']" disabled />
          </label>
          <label>
            <span>保留天数</span><input :value="trialModal.storage?.['保留天数']" disabled />
          </label>
          <label>
            <span>备份策略</span><input :value="trialModal.storage?.['备份策略']" disabled />
          </label>
        </div>

        <div class="modal-foot" style="justify-content: flex-start">
          <button class="btn primary" type="button" :disabled="trialLoading" @click="runTrial(false)">
            {{ trialLoading ? '试算中…' : '发起全量试算' }}
          </button>
          <button class="btn" type="button" :disabled="trialLoading" @click="runTrial(true)">模拟中断（每批1个分区）</button>
          <button
            v-if="trialModal.trial?.['状态'] === '试算中'"
            class="btn"
            type="button"
            @click="resumeTrial"
          >从上次分区继续（{{ trialModal.trial['断点续算']?.['上次分区'] }}）</button>
        </div>

        <template v-if="trialModal.trial">
          <p class="section-desc">
            试算单 {{ trialModal.trial['试算编号'] }} · 配置版本 v{{ trialModal.trial['配置版本'] }} ·
            状态 <span :class="badgeClass(trialModal.trial['状态'])">{{ trialModal.trial['状态'] }}</span>
          </p>
          <div v-if="trialModal.trial['汇总'] && Object.keys(trialModal.trial['汇总']).length" class="board-grid">
            <div v-for="(value, label) in trialModal.trial['汇总']" :key="label" class="board-card">
              <span class="stat-label">{{ label }}</span><strong>{{ value }}</strong>
            </div>
          </div>

          <h4 class="section-title">清理时间线</h4>
          <div class="timeline-wrap">
            <div v-for="line in trialModal.trial['时间线']" :key="String(line['分区id'])" class="timeline-item">
              <div>
                <strong>{{ line['分区编号'] }}</strong>
                <span :class="badgeClass(line['分区状态'])" style="margin-left: 8px">{{ line['分区状态'] }}</span>
                <div class="timeline-meta" style="margin-top: 6px">
                  <span>采集时间：{{ line['采集时间'] }}</span>
                  <span>分区年龄：{{ line['分区年龄'] }} 天</span>
                  <span>保留天数：{{ line['保留天数'] }}</span>
                  <span>清理日期：{{ line['清理日期'] }}</span>
                  <span>记录条数：{{ line['记录条数'] }}</span>
                  <span>待清理条数：{{ line['待清理条数'] }}</span>
                </div>
                <table v-if="line['明细桶']?.length" class="data-table sub-table" style="margin-top: 8px">
                  <thead>
                    <tr><th>采集时间</th><th>记录条数</th><th>桶状态</th><th>清理日期</th></tr>
                  </thead>
                  <tbody>
                    <tr v-for="bucket in line['明细桶']" :key="bucket['采集时间']">
                      <td>{{ bucket['采集时间'] }}</td>
                      <td>{{ bucket['记录条数'] }}</td>
                      <td><span :class="badgeClass(bucket['桶状态'])">{{ bucket['桶状态'] }}</span></td>
                      <td>{{ bucket['清理日期'] }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          <h4 class="section-title">待清理清单</h4>
          <table class="data-table sub-table">
            <thead>
              <tr><th>分区编号</th><th>采集时间</th><th>清理日期</th><th>待清理条数</th><th>数据量(MB)</th></tr>
            </thead>
            <tbody>
              <tr v-for="item in trialModal.trial['待清理清单']" :key="item['分区编号']">
                <td>{{ item['分区编号'] }}</td>
                <td>{{ item['采集时间'] }}</td>
                <td>{{ item['清理日期'] }}</td>
                <td>{{ item['待清理条数'] }}</td>
                <td>{{ item['数据量MB'] }}</td>
              </tr>
              <tr v-if="!trialModal.trial['待清理清单']?.length">
                <td colspan="5" class="empty-state">当前没有超出保留天数的分区</td>
              </tr>
            </tbody>
          </table>

          <div class="modal-foot">
            <button
              class="btn"
              type="button"
              :disabled="trialModal.trial['状态'] === '试算中'"
              @click="cancelTrial"
            >取消试算（恢复原状）</button>
            <button
              class="btn primary danger"
              type="button"
              :disabled="trialModal.trial['状态'] !== '待确认'"
              @click="confirmTrial"
            >确认清理（只删超期记录）</button>
          </div>
        </template>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | boolean | null>
type BoardRow = Record<string, string | number | null>

interface BucketRow {
  采集时间: string
  记录条数: number
  桶状态: string
  清理日期: string
}

interface TimelineRow {
  分区编号: string
  分区id: number
  采集时间: string
  保留天数: number
  清理日期: string
  分区年龄: number
  分区状态: string
  记录条数: number
  待清理条数: number
  数据量MB: number
  明细桶: BucketRow[]
}

interface WaitingRow {
  分区编号: string
  采集时间: string
  清理日期: string
  待清理条数: number
  数据量MB: number
}

interface Checkpoint {
  已完成分区: number[]
  上次分区: string | null
  是否完成: boolean
}

interface TrialRow {
  id: number
  试算编号: string
  存储编号?: string
  存储id?: number
  配置版本: number
  状态: string
  时间线: TimelineRow[]
  待清理清单: WaitingRow[]
  汇总: Record<string, number>
  断点续算: Checkpoint
  试算时间?: string
  备注?: string
  确认时间?: string
  取消时间?: string
}

const ENDPOINT = '/api/storage'
const columns = ['存储编号', '存储类型', '连接串', '压缩方式', '分区策略', '保留天数', '备份策略', '是否启用']
const editableFields = ['存储类型', '连接串', '压缩方式', '分区策略', '保留天数', '备份策略', '是否启用']

const rows = ref<Row[]>([])
const total = ref(0)
const keyword = ref('')
const statusFilter = ref('')
const errorMessage = ref('')
const successMessage = ref('')
const saving = ref(false)
const trialLoading = ref(false)

const boardItems = ref<BoardRow[]>([])
const boardSummary = ref<Record<string, number>>({})
const cleanupItems = ref<BoardRow[]>([])

const configModal = reactive<{ open: boolean; storageId: number | null; form: Record<string, string | number> }>({
  open: false,
  storageId: null,
  form: {},
})

const trialModal = reactive<{
  open: boolean
  storage: Row | null
  trial: TrialRow | null
}>({ open: false, storage: null, trial: null })

function badgeClass(status: string | number | null): string {
  const key = String(status)
  const map: Record<string, string> = {
    待清理: 'badge wait',
    待确认: 'badge confirm',
    观察中: 'badge watch',
    已清理: 'badge done',
    已撤销: 'badge revoke',
    试算中: 'badge running',
    已取消: 'badge revoke',
    已确认: 'badge done',
  }
  return map[key] ?? 'badge revoke'
}

function flash(message: string, ok = true) {
  if (ok) {
    successMessage.value = message
    errorMessage.value = ''
  } else {
    errorMessage.value = message
    successMessage.value = ''
  }
}

function resetFilters() {
  keyword.value = ''
  statusFilter.value = ''
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

async function runAction(action: string, row: Row) {
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const payload = await response.json()
    if (!payload.ok) throw new Error(payload.message)
    flash(payload.message)
    await reload()
  } catch (error) {
    flash(error instanceof Error ? error.message : '存储配置动作未生效', false)
  }
}

function openConfig(row: Row) {
  configModal.storageId = Number(row.id)
  configModal.form = { ...editableFields.reduce<Record<string, string | number>>((acc, field) => {
    acc[field] = (row[field] as string | number) ?? ''
    return acc
  }, {}) }
  configModal.open = true
}

async function saveConfig() {
  if (configModal.storageId === null) return
  saving.value = true
  try {
    const values = { ...configModal.form }
    if (values['保留天数'] !== undefined && values['保留天数'] !== null && values['保留天数'] !== '') {
      values['保留天数'] = Number(values['保留天数'])
    }
    const response = await request(`${ENDPOINT}/${configModal.storageId}`, {
      method: 'PATCH',
      body: JSON.stringify({ values }),
    })
    const payload = await response.json()
    if (!payload.ok) throw new Error(payload.message)
    flash(payload.message)
    configModal.open = false
    await reload()
  } catch (error) {
    flash(error instanceof Error ? error.message : '存储配置更新失败', false)
  } finally {
    saving.value = false
  }
}

function openTrial(row: Row) {
  trialModal.open = true
  trialModal.storage = row
  trialModal.trial = null
}

async function runTrial(interrupt: boolean) {
  if (!trialModal.storage) return
  trialLoading.value = true
  try {
    const response = await request(`${ENDPOINT}/${trialModal.storage.id}/trials`, {
      method: 'POST',
      body: JSON.stringify(interrupt ? { partition_limit: 1 } : {}),
    })
    const payload = await response.json()
    if (!payload.ok) throw new Error(payload.message)
    trialModal.trial = payload.entry
    flash(payload.message)
    await loadBoard()
  } catch (error) {
    flash(error instanceof Error ? error.message : '试算发起失败', false)
  } finally {
    trialLoading.value = false
  }
}

async function resumeTrial() {
  if (!trialModal.trial) return
  try {
    const response = await request(`${ENDPOINT}/trials/${trialModal.trial.id}/resume`, { method: 'POST' })
    const payload = await response.json()
    if (!payload.ok) throw new Error(payload.message)
    trialModal.trial = payload.entry
    flash(payload.message)
    await loadBoard()
  } catch (error) {
    flash(error instanceof Error ? error.message : '断点续算失败', false)
  }
}

async function confirmTrial() {
  if (!trialModal.trial) return
  try {
    const response = await request(`${ENDPOINT}/trials/${trialModal.trial.id}/confirm`, { method: 'POST' })
    const payload = await response.json()
    if (!payload.ok) throw new Error(payload.message)
    trialModal.trial = payload.entry
    flash(payload.message)
    await Promise.all([loadBoard(), loadCleanups(), reload()])
  } catch (error) {
    flash(error instanceof Error ? error.message : '确认清理失败', false)
  }
}

async function cancelTrial() {
  if (!trialModal.trial) return
  try {
    const response = await request(`${ENDPOINT}/trials/${trialModal.trial.id}/cancel`, { method: 'POST' })
    const payload = await response.json()
    if (!payload.ok) throw new Error(payload.message)
    trialModal.trial = payload.entry
    flash(payload.message)
    await Promise.all([loadBoard(), reload()])
  } catch (error) {
    flash(error instanceof Error ? error.message : '取消试算失败', false)
  }
}

async function loadBoard() {
  const response = await request(`${ENDPOINT}/cleanup-board`)
  if (!response.ok) return
  const payload = await response.json()
  boardItems.value = payload.items ?? []
  boardSummary.value = payload.summary ?? {}
}

async function loadCleanups() {
  const response = await request(`${ENDPOINT}/cleanups`)
  if (!response.ok) return
  const payload = await response.json()
  cleanupItems.value = payload.items ?? []
}

async function reload() {
  const query = new URLSearchParams()
  if (keyword.value) query.set('keyword', keyword.value)
  if (statusFilter.value) query.set('status', statusFilter.value)
  try {
    const response = await request(`${ENDPOINT}?${query.toString()}`)
    if (!response.ok) throw new Error('存储配置列表读取失败')
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    if (trialModal.storage) {
      trialModal.storage = rows.value.find((item) => item.id === trialModal.storage?.id) ?? trialModal.storage
    }
  } catch (error) {
    flash(error instanceof Error ? error.message : '采集数据存储列表读取失败', false)
  }
}

onMounted(() => {
  void reload()
  void loadBoard()
  void loadCleanups()
})
</script>
