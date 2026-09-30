<script setup lang="ts">
import { ref } from 'vue';
import { Save, X } from '@lucide/vue';
import { useInvestor } from './workspace';
const emit = defineEmits<{close: []}>();
const {state, busy, savePreferences, notify, showReset, showLogin, error, jobsByKind} = useInvestor();
const saveError = ref('');
const revision = state.value.settings.revision;
const cash = ref(state.value.settings.plan.starting_cash);
const automatic = ref(state.value.settings.plan.auto_update);
function close() { if (!busy.value) emit('close'); }
async function save() {
  saveError.value = '';
  if (await savePreferences({starting_cash: Number(cash.value), auto_update: automatic.value}, revision)) {
    notify('账户设置已保存，策略档位和参数保持不变');
    emit('close');
  } else if (showLogin.value) emit('close');
  else saveError.value = error.value;
}
</script>
<template>
  <div class="modal-backdrop" @click.self="close"><section class="dialog" role="dialog" aria-modal="true" aria-label="模拟账户设置" v-modal="close">
    <div class="section-heading"><h2>模拟账户设置</h2><button class="icon-button" aria-label="关闭账户设置" :disabled="busy" @click="close"><X :size="18"/></button></div>
    <form @submit.prevent="save">
      <p v-if="saveError" class="notice error" role="alert">{{ saveError }}</p>
      <label>模拟起始资金 / 元<input type="number" v-model.number="cash" min="1000" max="1000000000" required :disabled="!!state.started_at || busy"/></label>
      <p v-if="state.started_at" class="small-note">已有模拟记录，起始资金已锁定。归档后可为新账户设置资金。</p>
      <label class="check-field"><input type="checkbox" v-model="automatic" :disabled="busy"/><span>自动更新模拟账户</span></label>
      <p class="small-note">{{ state.settings.plan.research_interval === 'daily' ? '工作日 16:00 后检查新行情。' : '交易时段每根 K 线完成后检查新行情。' }}电脑与服务需保持运行；网页可以关闭，不会向券商下单。</p>
      <div class="dialog-actions"><button type="button" :disabled="busy" @click="close">取消</button><button type="submit" class="primary" :disabled="busy"><Save :size="16"/>保存账户设置</button></div>
    </form>
    <details class="section"><summary>归档并重新开始</summary><p class="small-note">归档当前项目模拟，保留历史记录。不操作券商账户。</p><button class="danger-text" :disabled="busy || !state.started_at || jobsByKind('portfolio_update')" @click="emit('close'); showReset = true">归档当前模拟</button></details>
  </section></div>
</template>
