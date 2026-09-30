<script setup lang="ts">
import { computed, inject, ref } from "vue";
import { X, RotateCcw, Check } from "@lucide/vue";
import { preset, modelName } from "../workspace";
import { api, clone } from "../api";
const w = inject<any>("workspace"),
  tab = ref("signal");
const p = computed(() => preset(w.draft));
const riskFields = [
  { key: "starting_cash", name: "回测起始资金", step: 1000 },
  { key: "max_trade_cash", name: "单笔金额上限", step: 1000 },
  { key: "max_order_shares", name: "单笔份数上限", step: 100 },
  { key: "max_position_pct", name: "单标的最高仓位", step: 5, percent: true },
  { key: "stop_loss_pct", name: "止损幅度", step: 1, percent: true },
  { key: "take_profit_pct", name: "止盈幅度", step: 5, percent: true },
  {
    key: "max_daily_loss_pct",
    name: "单日新增买入停止线",
    step: 0.5,
    percent: true,
  },
];
function riskValue(key: string, value: string, percent: boolean | undefined) {
  w.draft.risk[key] = Number(value) / (percent ? 100 : 1);
}
async function reset() {
  await w.act(async () => {
    w.catalog = await api("/catalog");
    const defaults = w.catalog.defaults;
    if (w.editor === "account") w.draft.risk = clone(defaults.risk);
    else if (w.draft.engine === "ml")
      w.draft.models[w.draft.model][w.draft.profile] = clone(
        defaults.models[w.draft.model][w.draft.profile],
      );
    else
      w.draft[w.draft.engine][w.draft.profile] = clone(
        defaults[w.draft.engine][w.draft.profile],
      );
  });
}
async function finish() {
  if (w.editor === "account") {
    if (await w.save()) w.editor = "";
  } else {
    w.editor = "";
    await w.refreshEvidence();
  }
}
</script>
<template>
  <div class="modal-backdrop editor-backdrop" @click.self="w.editor = ''">
    <section
      class="parameter-editor"
      role="dialog"
      aria-modal="true"
      aria-label="参数编辑器"
      v-modal="() => (w.editor = '')"
      @keydown.esc="w.editor = ''"
    >
      <header class="editor-header">
        <div>
          <span class="overline">{{
            w.editor === "account" ? "ACCOUNT" : "PARAMETERS"
          }}</span>
          <h2>
            {{
              w.editor === "account"
                ? "资金与交易限制"
                : `${modelName(w.draft)} · ${w.catalog.profiles[w.draft.profile]}`
            }}
          </h2>
        </div>
        <button class="icon-btn" title="关闭参数编辑器" @click="w.editor = ''">
          <X :size="21" />
        </button>
      </header>
      <div
        v-if="w.editor === 'strategy' && w.draft.engine === 'ml'"
        class="section-tabs"
      >
        <button
          v-for="item in [
            ['signal', '买卖判断'],
            ['training', '训练数据'],
            ['model', '模型参数'],
          ]"
          :class="{ active: tab === item[0] }"
          @click="tab = item[0]"
        >
          {{ item[1] }}
        </button>
      </div>
      <div class="editor-body">
        <template v-if="w.editor === 'account'"
          ><div class="field-grid">
            <label v-for="f in riskFields" class="field"
              ><span
                >{{ f.name
                }}{{
                  f.percent
                    ? " / %"
                    : f.key === "max_order_shares"
                      ? " / 份"
                      : " / 元"
                }}</span
              ><input
                type="number"
                :value="
                  Number(
                    (w.draft.risk[f.key] * (f.percent ? 100 : 1)).toFixed(8),
                  )
                "
                :step="f.step"
                min="0"
                @input="
                  riskValue(
                    f.key,
                    ($event.target as HTMLInputElement).value,
                    f.percent,
                  )
                "
            /></label>
          </div>
          <details class="fold">
            <summary>成交成本与随机种子</summary>
            <div class="field-grid inner">
              <label class="field"
                ><span>手续费率 / %</span
                ><input
                  type="number"
                  :value="w.draft.risk.commission_rate * 100"
                  step="0.001"
                  @input="
                    riskValue(
                      'commission_rate',
                      ($event.target as HTMLInputElement).value,
                      true,
                    )
                  " /></label
              ><label class="field"
                ><span>滑点 / 基点</span
                ><input
                  type="number"
                  v-model.number="w.draft.risk.slippage_bps"
                  min="0"
                  step="1" /></label
              ><label class="field"
                ><span>随机种子</span
                ><input type="number" v-model.number="w.draft.seed"
              /></label>
            </div>
          </details>
          <p class="notice">更改回测起始资金不会重置当前模拟账户。</p></template
        >
        <template v-else>
          <div
            v-if="tab === 'signal' || w.draft.engine !== 'ml'"
            class="field-grid"
          >
            <template v-if="w.draft.engine === 'ml'"
              ><label class="field"
                ><span>买入门槛</span
                ><input
                  v-model.number="p.buy_threshold"
                  type="number"
                  min="0"
                  max="1"
                  step="0.01"
                /><small>更高：更少的买入机会。</small></label
              ><label class="field"
                ><span>卖出门槛</span
                ><input
                  v-model.number="p.sell_threshold"
                  type="number"
                  min="0"
                  max="1"
                  step="0.01"
                /><small>更高：更容易卖出，须低于买入门槛。</small></label
              ></template
            >
            <label v-if="w.draft.engine === 'local'" class="field"
              ><span>本地买入分</span
              ><input
                v-model.number="p.buy_score"
                type="number"
                min="0"
                max="6"
              /><small>趋势、动量、RSI、成交量共同评分。</small></label
            >
            <label class="field"
              ><span>每日最多买入标的数</span
              ><input type="number" v-model.number="p.max_buys" min="0"
            /></label>
            <label class="check-field"
              ><input
                type="checkbox"
                v-model="p.allow_add"
              />允许持仓继续加仓</label
            >
            <label v-if="w.draft.engine === 'ml'" class="check-field"
              ><input
                type="checkbox"
                v-model="p.calibrate_regime"
              />按行情校准买卖门槛</label
            >
            <label v-if="w.draft.engine === 'ai'" class="field span-all"
              ><span>AI 判断提示词</span
              ><textarea
                rows="9"
                maxlength="5000"
                v-model="p.ai_prompt"
              ></textarea>
            </label>
            <template
              v-if="
                w.draft.engine === 'ai' &&
                w.capabilities.includes('ml_ai_review')
              "
            >
              <p
                v-if="w.capabilities.includes('ml_reference_panel15')"
                class="notice span-all"
              >
                AI 综合查看五种机器学习模型的激进、均衡、保守档，共 15
                组参考；参数在各模型档位中分别调整。自定义档不参与参考。
              </p>
              <template v-else>
                <label class="check-field"
                  ><input
                    type="checkbox"
                    v-model="p.use_ml_reference"
                  />机器学习提供参考，AI 复核</label
                >
                <label class="field"
                  ><span>参考模型</span
                  ><select
                    v-model="p.reference_model"
                    :disabled="p.use_ml_reference === false"
                  >
                    <option
                      v-for="(name, key) in w.catalog.models"
                      :value="key"
                    >
                      {{ name }}
                    </option>
                  </select></label
                >
              </template>
            </template>
          </div>
          <div
            v-if="tab === 'training' && w.draft.engine === 'ml'"
            class="field-grid"
          >
            <label class="field"
              ><span>历史训练窗口</span
              ><select v-model="p.training_years">
                <option v-for="year in [1, 3, 5, 10]" :value="year">
                  {{ year }} 年
                </option></select
              ><small>测试日期固定，训练只使用测试前的历史。</small></label
            ><label class="field"
              ><span>样本来源</span
              ><select v-model="p.training_mode">
                <option value="self">优先学习标的自身</option>
                <option value="classified">按牛熊震荡分类</option>
                <option value="mixed">全部标的混合</option></select
              ><small>样本不足时逐级尝试分类、混合及规则回退。</small></label
            ><label class="field"
              ><span>预测目标</span
              ><select v-model="p.target">
                <option value="binary">二分类：收益超过 0.1% / 未超过</option>
                <option value="trend5">五种走势</option>
              </select></label
            ><label v-if="p.label_basis !== undefined" class="field"
              ><span>训练收益口径</span
              ><select v-model="p.label_basis">
                <option value="close_return">收盘涨跌（旧版对照）</option>
                <option value="next_open_net">下一开盘成交净收益</option>
              </select></label
            ><label class="field"
              ><span>预测跨度 / 交易日</span
              ><input
                type="number"
                v-model.number="p.horizon"
                min="1"
                max="20" /></label
            ><label class="field"
              ><span>最小训练样本</span
              ><input
                type="number"
                v-model.number="p.min_samples"
                min="20"
                step="10"
            /></label>
          </div>
          <div
            v-if="tab === 'model' && w.draft.engine === 'ml'"
            class="field-grid"
          >
            <label
              v-for="f in w.catalog.parameters[w.draft.model]"
              class="field"
              ><span>{{ f.label }}</span
              ><select v-if="f.kind === 'select'" v-model="p.params[f.key]">
                <option v-for="option in f.options" :value="option">
                  {{
                    { auto: "自动", cpu: "CPU", cuda: "GPU · CUDA" }[
                      option as "auto"
                    ] || option
                  }}
                </option></select
              ><label v-else-if="f.kind === 'boolean'" class="check-field"
                ><input type="checkbox" v-model="p.params[f.key]" />启用</label
              ><input
                v-else
                type="number"
                v-model.number="p.params[f.key]"
                :min="f.min"
                :max="f.max"
                :step="f.step"
              /><small>{{ f.help }}</small></label
            >
          </div>
        </template>
      </div>
      <footer class="editor-footer">
        <button
          class="subtle"
          @click="reset"
          :disabled="!w.access.write_allowed"
        >
          <RotateCcw :size="15" />恢复这一组默认</button
        ><button
          class="primary"
          :disabled="w.busy || !w.access.write_allowed"
          @click="finish"
        >
          <Check :size="16" />{{
            w.editor === "account" ? "保存并启用" : "完成编辑"
          }}
        </button>
      </footer>
    </section>
  </div>
</template>
