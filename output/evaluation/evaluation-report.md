# EvoAgent 端到端 Evaluation Harness 报告

## 数据集

- 样本：100 个 PR Diff（40 风险，60 干净）
- 仓库：10 个，按仓库划分 Validation/Holdout
- 来源标记：`synthetic-controlled`
- SHA-256：`88831bb19264f9fc15433de7801b623aad38b80076f5d5b085d0299fd40cc115`

> 注意：本次离线数据是受控合成基准，用于验证评测代码和计算口径，不能表述为 100 个真实公开 PR 的生产效果。

## 总体结果

| 指标 | 单 Agent 基线 | 多 Agent 候选 | 变化 |
|---|---:|---:|---:|
| Precision | 83.3% | 82.5% | -0.8 pp |
| Recall | 62.5% | 82.5% | +20.0 pp |
| F1 | 71.4% | 82.5% | +11.1 pp |
| 严重等级准确率 | 100.0% | 100.0% | +0.0 pp |
| 高风险召回率 | 84.2% | 94.7% | +10.5 pp |
| 干净 PR 准确率 | 91.7% | 91.7% | +0.0 pp |
| 执行成功率 | 100.0% | 100.0% | +0.0 pp |
| 自动修复验证通过率 | — | 78.8% | — |
| 端到端安全修复成功率 | — | 65.0% | — |

计数：基线 TP/FP/FN = 25/5/15；候选 TP/FP/FN = 33/7/7。

自动修复：候选命中的 33 个风险中，26 个通过风险复现、补丁生成、编译、风险消除和回归门禁；全部 40 个风险样本中 26 个实现端到端成功。

## 分区结果

| 分区 | 样本 | 风险/干净 | F1 | 高风险召回率 | 干净准确率 |
|---|---:|---:|---:|---:|---:|
| Validation | 80 | 32/48 | 83.6% | 100.0% | 89.6% |
| Holdout | 20 | 8/12 | 76.9% | 66.7% | 100.0% |

## 指标口径

- 一对一匹配：路径相同、CWE 相同，预测行位于标注区间或距离不超过 2 行。
- 重复预测只能匹配一次，其余计为 FP。
- 严重等级准确率仅在 TP 上计算，并要求等级完全一致。
- 干净准确率按 PR 计算：干净 PR 完全没有报告才算正确。
- 修复通过要求五个门禁全部成功：风险复现、补丁生成、编译、风险消除、回归检查。

## 发布门禁

数值门禁：**通过**

生产激活：**阻止；需使用带独立真值的真实公开 PR 数据集**

- `validation_f1_improvement`：PASS
- `high_risk_recall_non_regression`：PASS
- `clean_accuracy_non_regression`：PASS
- `holdout_f1_non_regression`：PASS
- `execution_success`：PASS
- `safe_fix_rate`：PASS
- `e2e_security_fix_rate`：PASS
- `production_data_provenance`：FAIL
