<script setup lang="ts">
import { ArrowLeft, ChartNoAxesCombined, SlidersHorizontal, History, Play, Square, RefreshCw, RotateCcw, Download, X, LockKeyhole, FlaskConical, ChevronLeft, ChevronRight, Info } from '@lucide/vue';
import { computed } from 'vue';
import { createResearchWorkspace, fieldNames, fieldHints, slotNames, modelNames } from './research/workspace';
import { dateTime, statusName, pct, money } from './api';
import StudyResult from './research/StudyResult.vue';
import './investor.css';
import './research/research.css';
const w = createResearchWorkspace();
const {page, context, error, busy, loading, connected, writable, showLogin, loginKey, slot, recipe,
  compareRecipes, form, jobs, total, offset, selected, detailId, baseline, spec, fields, tuning, active, canRun,
  navigate, resetTuning, selectJob, paginate, run, jobAction, login, exportJson, repeat, legacy, legacyResult, legacyKind, legacyOffset, legacyTotal, loadLegacy, inspectLegacy} = w;
const tabs = [{id:'compare', name:'方案对比', icon:ChartNoAxesCombined}, {id:'tune',name:'参数优化',icon:SlidersHorizontal}, {id:'history',name:'研究记录',icon:History}];
const algorithmChoices = [
  {key:'rotation',name:'趋势分散',tag:'规则基准',detail:'多周期趋势 · 不训练模型'},
  {key:'ridge20',name:'Ridge',tag:'线性回归',detail:'低复杂度 · 强正则'},
  {key:'hgb20_regularized',name:'HistGradientBoosting',tag:'梯度树',detail:'非线性关系 · 强正则'},
  {key:'extra20',name:'Extra Trees',tag:'随机树集成',detail:'随机切分 · 平滑预测'},
  {key:'xgb20',name:'XGBoost',tag:'梯度提升',detail:'逐步修正误差 · 需完整模型环境'},
  {key:'cnn20',name:'1D-CNN',tag:'序列网络',detail:'连续时序特征 · 计算较慢'},
];
const selectedTitle = computed(() => selected.value?.request?.study?.mode === 'tune' ? '参数优化' : '方案对比');
const baselineSignal = computed(() => {
  const p = baseline.value?.plan;
  return p?.method === 'ml' && p.model_recipe !== 'legacy'
    ? (context.value?.recipes[p.model_recipe]?.label || p.model_recipe) + (p.model_tuning ? ' · 研究参数' : '')
    : ({rotation:'趋势分散', hold:'持有基准', ml:'基础梯度树回归', ml_class:'梯度树分类', cnn:'CNN 回归', cnn_class:'CNN 分类'} as Record<string,string>)[p?.method];
});
function bounds(key: string) { return context.value?.parameter_schema[key]?.anyOf?.find((x:any) => x.type !== 'null') || {}; }
async function reload() { try {await w.reloadContext(); await w.refreshJobs();} catch (e:any) {error.value = e.message;} }
</script>

<template>
  <div class="research-shell">
    <header class="research-header">
      <a class="brand" href="/?lab=1#compare"><FlaskConical :size="21"/><strong>StockLab</strong><span>研究</span></a>
      <nav class="research-nav" aria-label="研究导航"><button v-for="tab in tabs" :key="tab.id" :class="{selected:page === tab.id}" @click="navigate(tab.id)"><component :is="tab.icon" :size="17"/>{{ tab.name }}</button></nav>
      <a class="research-return" href="/#overview"><ArrowLeft :size="16"/>返回投资</a>
    </header>
    <main class="research-main">
      <div v-if="error" class="notice error" role="alert"><span>{{ error }}</span><button class="icon-button" aria-label="关闭错误" @click="error = ''"><X :size="16"/></button></div>
      <div v-if="!connected" class="notice warning">服务连接中断。已读取的参数和结果仍保留。<button @click="reload">重试</button></div>
      <div v-if="loading" class="research-empty">正在读取研究配置…</div>
      <template v-else-if="context">
        <div class="research-page-heading"><div><small>RESEARCH WORKSPACE</small><h1>{{ tabs.find(t => t.id === page)?.name }}</h1></div><div class="research-actions"><span class="research-status">{{ connected ? '本地研究' : '离线' }}</span><button class="icon-button" title="重新读取基线和记录" aria-label="重新读取基线和记录" @click="reload"><RefreshCw :size="17"/></button><button v-if="!writable" class="icon-button" title="解锁研究操作" aria-label="解锁研究操作" @click="showLogin = true"><LockKeyhole :size="17"/></button></div></div>
        <template v-if="page !== 'history'">
          <section class="research-baseline">
            <label>共同投资计划<select v-model="slot" aria-label="共同投资计划"><option v-for="(name, key) in slotNames" :key="key" :value="key">{{ name }}</option></select></label>
            <div class="research-baseline-summary"><strong>{{ baseline.plan.name }}</strong><span>{{ baselineSignal }} · {{ money(baseline.plan.starting_cash) }} 元 · {{ baseline.plan.research_multiple }}x · {{ {daily:'日线', '30m':'30分钟', '60m':'60分钟'}[baseline.plan.research_interval as string] }} · {{ {daily:'每日',weekly:'每周',monthly:'每月'}[baseline.plan.rebalance as string] }}调仓</span><small>计划 {{ baseline.plan_key.slice(0, 8) }}</small></div>
            <details class="research-conditions"><summary>共同条件</summary><dl><dt>仓位上限</dt><dd>{{ pct(baseline.plan.max_exposure) }}</dd><dt>单只上限</dt><dd>{{ pct(baseline.plan.max_weight) }}</dd><dt>佣金 / 最低佣金</dt><dd>{{ pct(baseline.plan.commission_rate) }} / {{ baseline.plan.min_commission }} 元</dd><dt>滑点 / 融资年利率</dt><dd>{{ baseline.plan.slippage_bps }} bps / {{ pct(baseline.plan.research_annual_rate) }}</dd><dt>投资范围</dt><dd>{{ baseline.plan.symbols.map((s:string) => context.assets[s]?.name || s).join('、') }}</dd></dl></details>
          </section>
          <div v-if="baseline.plan.research_interval !== 'daily'" class="notice warning">此工作台研究日线模型。所选为盘中计划，请换用已保存的日线计划；此处不会自动更改交易粒度。</div>
          <div class="research-compose">
            <section class="research-controls">
              <h2>{{ page === 'tune' ? '检验设计' : '测试区间' }}</h2>
              <div class="research-field-pair"><label>{{ page === 'tune' ? '调参起始年' : '起始年' }}<input type="number" v-model.number="form.start_year" min="2015" :max="new Date().getFullYear() - 1"/></label><label>{{ page === 'tune' ? '调参结束年' : '结束年' }}<input type="number" v-model.number="form.end_year" min="2015" :max="new Date().getFullYear() - 1"/></label></div>
              <template v-if="page === 'tune'"><div class="research-field-pair"><label>后续检验起始年<input type="number" v-model.number="form.check_start_year" :min="form.end_year + 1" :max="new Date().getFullYear() - 1"/></label><label>后续检验结束年<input type="number" v-model.number="form.check_end_year" :min="form.check_start_year" :max="new Date().getFullYear() - 1"/></label></div><label>搜索预算<select v-model.number="form.budget"><option :value="3">小范围 · 3 个中心与邻近候选</option><option :value="5">标准 · 5 个中心与邻近候选</option><option :value="9">扩展 · 最多 9 个中心与邻近候选</option></select></label></template>
              <dl class="research-facts"><dt>参照</dt><dd>原计划、同配置持有</dd><dt>成交与成本</dt><dd>次日开盘 · 佣金 · 滑点 · 利息</dd><dt>训练</dt><dd>按月滚动 · 标签时间截断</dd></dl>
              <button class="primary research-run" :disabled="!canRun || !!active || !compareRecipes.length" @click="run"><Play :size="16"/>{{ busy ? '正在提交' : page === 'tune' ? '开始参数优化' : '开始对比' }}</button>
              <a v-if="active" href="#history" class="research-link">研究任务进行中 · 查看进度</a>
            </section>
            <section class="research-models" v-if="page === 'compare'">
              <div class="research-heading"><h2>待比较方案</h2><small>{{ compareRecipes.length }} 项 + 共同基准</small></div>
              <label v-for="algorithm in algorithmChoices" :key="algorithm.key" class="research-model-row"><input type="checkbox" v-model="compareRecipes" :value="algorithm.key"/><span><strong>{{ algorithm.name }}</strong><small>{{ algorithm.detail }}</small></span><span class="research-tag">{{ algorithm.tag }}</span></label>
              <details class="research-disclosure"><summary>其他已有配方</summary><div class="research-extra-recipes"><label v-for="(s, key) in context.recipes" :key="key" v-show="!algorithmChoices.some(a => a.key === key)"><input type="checkbox" v-model="compareRecipes" :value="key"/>{{ s.label }}</label></div></details>
            </section>
            <section v-else class="research-models">
              <div class="research-heading"><h2>模型参数</h2><button class="icon-button" title="恢复该配方默认参数" aria-label="恢复该配方默认参数" @click="resetTuning"><RotateCcw :size="17"/></button></div>
              <label>模型配方<select v-model="recipe" aria-label="研究模型配方"><option v-for="(s, key) in context.recipes" :key="key" :value="key">{{ s.label }}</option></select></label>
              <div class="research-parameter-title"><strong>{{ modelNames[spec.model] }}</strong><span class="research-tag">预测未来净持有收益</span></div>
              <div class="research-parameter-grid"><label v-for="key in fields" :key="key"><span>{{ fieldNames[key] }} <Info :size="13" :title="fieldHints[key]"/></span><input type="number" v-model.number="tuning[key]" :min="bounds(key).minimum" :max="bounds(key).maximum" :step="key === 'regularization' ? .1 : 1" :title="fieldHints[key]"/></label></div>
            </section>
          </div>
        </template>
        <div v-else class="research-history-layout">
          <aside class="research-history-list"><div class="research-heading"><h2>已保存研究</h2><small>{{ total }} 项</small></div><div v-if="!jobs.length" class="research-empty">还没有研究记录</div><button v-for="job in jobs" :key="job.id" class="research-history-item" :class="{chosen: detailId === job.id}" @click="selectJob(job.id)"><strong>{{ job.summary.name }}</strong><span>{{ statusName(job.status) }} · {{ dateTime(job.created) }}</span><small>{{ job.summary.years?.[0] }}–{{ job.summary.years?.at(-1) }} · {{ job.id.slice(0,8) }}</small></button><div class="research-pagination"><button class="icon-button" :disabled="offset === 0" aria-label="上一页" @click="paginate(-1)"><ChevronLeft :size="17"/></button><small>{{ total ? offset + 1 : 0 }}–{{ Math.min(offset + 12, total) }} / {{ total }}</small><button class="icon-button" :disabled="offset + 12 >= total" aria-label="下一页" @click="paginate(1)"><ChevronRight :size="17"/></button></div>
            <details class="research-disclosure" @toggle="($event.target as HTMLDetailsElement).open && loadLegacy()"><summary>旧版记录（只读）</summary><label>记录类型<select v-model="legacyKind"><option value="evaluate">跨年评价</option><option value="learn">自动学习</option><option value="backtest">历史回测</option></select></label><button v-for="job in legacy" :key="job.id" class="research-history-item" @click="inspectLegacy(job.id)">{{ job.summary.name }} · {{ dateTime(job.created) }}</button><div class="research-pagination"><button class="icon-button" :disabled="legacyOffset === 0" aria-label="旧记录上一页" @click="loadLegacy(-1)"><ChevronLeft :size="16"/></button><small>{{ legacyTotal }} 项</small><button class="icon-button" :disabled="legacyOffset + 12 >= legacyTotal" aria-label="旧记录下一页" @click="loadLegacy(1)"><ChevronRight :size="16"/></button></div><template v-if="legacyResult"><p>{{ legacyResult.summary.name }} · {{ statusName(legacyResult.status) }}</p><small>旧执行规则，不参与新版排名</small><button @click="exportJson(legacyResult)"><Download :size="16"/>导出旧记录</button></template></details>
          </aside>
          <section class="research-history-detail">
            <template v-if="selected"><div class="research-heading"><div><h2>{{ selectedTitle }}</h2><small>{{ dateTime(selected.created) }} · {{ selected.id.slice(0,8) }}</small></div><div class="research-actions"><button class="icon-button" title="复制参数到新研究" aria-label="复制参数到新研究" @click="repeat"><RotateCcw :size="17"/></button><button class="icon-button" title="导出完整研究" aria-label="导出完整研究" @click="exportJson()"><Download :size="17"/></button></div></div>
              <div class="research-job"><div><strong>{{ statusName(selected.status) }}</strong><span>{{ selected.message }}</span></div><progress max="100" :value="selected.progress"/><small>{{ Math.round(selected.progress) }}%</small><button v-if="['queued','running'].includes(selected.status)" class="icon-button" aria-label="停止研究" title="停止研究" :disabled="busy" @click="jobAction('stop')"><Square :size="16"/></button><button v-else-if="['stopped','failed','interrupted'].includes(selected.status)" :disabled="busy" @click="jobAction('resume')"><Play :size="16"/>继续</button></div>
              <div v-if="selected.error" class="notice error">{{ selected.error }}</div>
              <StudyResult v-if="selected.result" :job="selected"/><div v-else class="research-empty"><ChartNoAxesCombined :size="32"/><h3>等待首个候选结果</h3><span>任务已保存到本机服务</span></div>
            </template><div v-else class="research-empty"><FlaskConical :size="36"/><h3>从一次公平对比开始</h3><button @click="navigate('compare')">新建方案对比</button></div>
          </section>
        </div>
        <footer class="research-footer">研究不下单 · 不自动修改投资计划 · 历史表现不代表未来收益</footer>
      </template>
    </main>
    <div v-if="showLogin" class="modal-backdrop" @click.self="showLogin = false"><section class="dialog" role="dialog" aria-modal="true" aria-label="解锁研究操作" v-modal><div class="research-heading"><h2>解锁研究操作</h2><button class="icon-button" aria-label="关闭" @click="showLogin = false"><X :size="17"/></button></div><label>管理口令<input type="password" v-model="loginKey" autocomplete="current-password"/></label><button class="primary" @click="login">解锁</button></section></div>
  </div>
</template>
