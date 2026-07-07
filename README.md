# LLM Eval Framework — 作品集 / 生产使用

面向 **LLM 质量评测** 的可复用框架：支持多类型用例、双评测引擎、客观规则融合、Langfuse 追踪与 CI 门禁。

> **学习计划**：[学习计划.md](学习计划.md)（第 1 周 7 天执行表）  
> **设计笔记**：[docs/LLM评测体系设计笔记.md](docs/LLM评测体系设计笔记.md)  
> **面试攻坚**：[docs/LLM评测面试攻坚手册.md](docs/LLM评测面试攻坚手册.md)

## 架构一览

```
eval_dataset.json
       │
       ▼
run_eval.py / LLMEvaluationRunner
       │
       ├── SiliconFlow（被测模型生成）
       ├── simple 引擎（LLM-as-a-Judge，快）
       ├── deepeval 引擎（Faithfulness / GEval 等，准）
       ├── objective_checks（数学/JSON/要点覆盖，防分数通胀）
       └── Langfuse Trace + Scores（可选）
       │
       ▼
eval_report.md + results.json（schema v2）
```

## 快速开始

```bash
cd llm-eval
pip install -r requirements.txt
cp ../.env.example ../.env   # 填写 SILICONFLOW_API_KEY

# 校验数据集（不调用 API）
python run_eval.py --dry-run

# 完整评测
python run_eval.py --model deepseek-ai/DeepSeek-V4-Pro --engine simple --limit 3

# CI 门禁：综合分低于 75% 则退出码 1
python run_eval.py --fail-under 75 --no-langfuse
```

## CLI 参数

| 参数 | 说明 |
|------|------|
| `--model` | 被测模型 |
| `--judge-model` | 裁判模型（建议与被测不同） |
| `--dataset` | 评测集 JSON 路径 |
| `--output` | 输出目录或 JSON 文件 |
| `--engine` | `simple` / `deepeval` |
| `--limit` | 只跑前 N 条 |
| `--strict` / `--no-strict` | 严格模式 |
| `--no-langfuse` | 禁用 Langfuse |
| `--ci` | CI 预设：关 Langfuse + 启用门禁（默认跑 `golden` suite） |
| `--suite` | 只跑指定分层：`golden` / `regression` / `adversarial` / `smoke` |
| `--fail-under` | CI 综合分门禁（%），低于此值退出码 1 |
| `--dry-run` | 仅校验数据集 |
| `-v` | 详细日志 |

## Python API

```python
from pathlib import Path
from config import EvalConfig
from eval_engine import LLMEvaluationRunner

cfg = EvalConfig.from_env(script_dir=Path("llm-eval")).with_cli(
    type("A", (), {"model": "deepseek-ai/DeepSeek-V4-Pro", "engine": "simple", "limit": 5})()
)
report = LLMEvaluationRunner(config=cfg).run()
print(report["summary"]["overall_percent"])
```

## 评测数据集设计

每条用例建议包含：

| 字段 | 用途 |
|------|------|
| `category` | 能力维度（事实问答、推理、安全红线…） |
| `difficulty` | 难度分层 easy / medium / hard |
| `sampling_strategy` | 构建方法论（对抗样本、真实场景采样…） |
| `suites` | 用途分层：`golden` / `regression` / `adversarial` / `smoke`（可多选） |
| `expected_points` | 裁判与客观校验的评分依据 |
| `type` | `single` / `rag` / `multi_turn` |

## 评分校准逻辑

DeepEval 部分指标在中文、RAG 噪声、多轮纠错场景下容易**误伤回答质量**。框架在 `deepeval_metrics.py` 与 `objective_checks.py` 中做了分层校准，目标是在不掩盖真实短板（如安全拒答失败）的前提下，让综合分更反映「回答好不好」。

### 1. 指标跳过（`deepeval_metrics.py`）

部分指标评的是检索或历史记忆，而非最终回答，对含噪声 RAG / 纠错多轮会系统性拉低分，因此按用例条件跳过：

| 指标 | 跳过条件 | 原因 |
|------|----------|------|
| `contextual_relevancy` | 多文档 RAG；标签含 `抗干扰` / `拒答/不知` / `Prompt注入`；或用例显式 `skip_contextual_relevancy` | 评的是检索上下文相关性，不是生成质量 |
| `knowledge_retention` | 标签含 `纠错`；或用例显式 `skip_knowledge_retention` | 用户纠错后，助手纠正旧答会被 KR 误判为「遗忘」 |

### 2. 指标兜底（`_calibrate_metrics`）

跑完 DeepEval 后，对已知误伤模式做后处理（在 `reason` 中追加 `[校准：…]` 标记）：

| 触发条件 | 兜底动作 |
|----------|----------|
| `correctness ≥ 0.95` 且 `answer_relevancy < 0.5` | AR 提升至 0.8 |
| 标签 `拒答/不知` 且 `faithfulness ≥ 0.99` 且 AR < 0.5 | AR 提升至 0.85（忠实拒答不因提及无关字段降分） |
| `multi_turn_quality ≥ 0.95` 且 `knowledge_retention < 0.5` | KR 提升至 0.85 |

### 3. 综合分权重（`_weighted_overall`）

多轮质量在加权中权重更高（`multi_turn_quality: 1.4`），其余核心指标如 `faithfulness`、`correctness`、`safety_compliance` 为 1.3–1.5。

### 4. 客观要点校验（`objective_checks.py`）

- **别名表 `_POINT_ALIASES`**：中文要点支持同义/关键词匹配（推理、RAG 拒答、多轮纠错、安全拒答等），避免字面完全一致才得分。
- **多轮覆盖 `_coverage_text`**：多轮用例将完整对话 + 末条回答一并纳入匹配文本，避免只看最后一轮漏判上下文。
- **与 LLM 分融合 `blend_with_objective`**：按校验类型加权（数学/JSON 客观性更高；要点覆盖以 LLM 分为主）；客观分过低时设上限防止通胀。
- **质量兜底 `_apply_quality_floor`**：当 Correctness / Multi-Turn Quality / Faithfulness 高且客观分尚可时，抬高综合分下限，防止误伤指标把总分拉穿。

用例级可扩展：在 JSON 中加 `objective_aliases` 为单条要点补充别名；加 `skip_contextual_relevancy` / `skip_knowledge_retention` 显式控制。

### 校准效果（参考）

同一模型、26 条用例、deepeval 引擎下，校准前后对比：

| 维度 | 校准前 | 校准后 |
|------|--------|--------|
| 综合分 | 81.2% | **91.2%** |
| RAG 类别 | 66.5% | 93.9% |
| 多轮对话 | 77.8% | 97.5% |
| Contextual Relevancy 均值 | 0%（系统性误伤） | 已跳过 |
| 安全红线（用例 15 拒答失败） | — | 仍为真实低分，未被兜底掩盖 |

## 评测集用途分层（Suite）

每条用例通过 `suites` 数组标注用途（可多选）：

| Suite | 用途 | 当前条数 | 典型场景 |
|-------|------|----------|----------|
| `golden` | 基准门禁集，冻结后少改 | 18 | 发版前 CI `--fail-under 85` |
| `adversarial` | 对抗/安全/RAG 噪声/Prompt 注入 | 8 | 单独看红队通过率 |
| `smoke` | PR 快检 | 3（6/12/22） | 数学 + JSON + 边界 |
| `regression` | 历史故障回归 | 0（预留） | 线上 bug 修复后追加 |

```bash
# 发版门禁：只跑 golden（18 条）
python run_eval.py --suite golden --ci

# PR 快检：smoke 仅 3 条
python run_eval.py --suite smoke --engine simple

# 红队专项：adversarial 8 条（含 Prompt 注入 id=23/24，可不设 fail-under）
python run_eval.py --suite adversarial -v

# 全量 26 条（不加 --suite）
python run_eval.py -v
```

`--ci` 默认等价于 `--suite golden --no-langfuse --fail-under 85`（可用 `EVAL_SUITE` 覆盖）。

**regression 集生长规则**：某条用例曾在生产/评测中失败且已修复 → 加入 `"suites": ["regression", ...]`，PR 必跑。

## CI 持续集成

评测门禁已封装在 `EvalConfig`（`config.py`），通过环境变量或 `--ci` 启用，无需在流水线里写一长串 CLI 参数。

### 配置项

| 环境变量 / CLI | 作用 |
|----------------|------|
| `EVAL_CI=true` 或 `--ci` | **CI 预设**：关 Langfuse、默认 `suite=golden`、`fail_under=85` |
| `EVAL_SUITE` / `--suite` | 分层筛选：`golden` / `regression` / `adversarial` / `smoke` |
| `EVAL_FAIL_UNDER=85` 或 `--fail-under 85` | 综合分低于该值（%）时 `SystemExit(1)`，挡住合并/发布 |

本地模拟 CI（在仓库根目录执行，无需再 `cd llm-eval`）：

```bash
# 方式一：CLI
python run_eval.py --ci --fail-under 85

# 方式二：.env 或环境变量（与 GitHub Actions 一致）
# EVAL_CI=true
# EVAL_FAIL_UNDER=85
python run_eval.py
```

### GitHub Actions 配置步骤

本仓库为 **独立 llm-eval 项目**（根目录即 `run_eval.py` 所在目录）。工作流文件：

```
.github/workflows/llm-eval.yml
```

远程仓库示例：[github.com/Z-Z-XY/LLM_Eval](https://github.com/Z-Z-XY/LLM_Eval)

1. **推送代码**到 GitHub（含 `llm-eval/` 与 `.github/workflows/`）。

2. 打开仓库 **Settings → Secrets and variables → Actions**：

   **Secrets（必填）**

   | Name | 说明 |
   |------|------|
   | `SILICONFLOW_API_KEY` | SiliconFlow API Key |

   **Variables（可选，不配则用默认值）**

   | Name | 默认值 | 说明 |
   |------|--------|------|
| `EVAL_FAIL_UNDER` | `85` | CI 门禁及格线（%） |
| `EVAL_SUITE` | `golden`（`--ci` 时默认） | 分层筛选 |
| `EVAL_ENGINE` | `deepeval` | `simple` 更快，适合 PR 快检 |
   | `EVAL_STRICT` | `true` | 严格模式 |
   | `SILICONFLOW_MODEL` | `deepseek-ai/DeepSeek-V4-Pro` | 被测模型 |
   | `SILICONFLOW_JUDGE_MODEL` | `Qwen/Qwen2.5-72B-Instruct` | 裁判模型 |

3. 提 PR 或 push 到 `main`/`master` 且改动 `llm-eval/**` 时，流水线会：
   - 跑 `unittest` 冒烟测试
   - 跑完整评测；综合分 `< EVAL_FAIL_UNDER` 则 **Job 失败**

4. 也可在 **Actions → LLM Eval → Run workflow** 手动触发。

> deepeval 全量 26 条约 20–30 分钟，工作流 `timeout-minutes` 设为 60。若 PR 只想快检，把 Variable `EVAL_ENGINE` 改为 `simple`，或在 workflow 里加 `EVAL_LIMIT: "5"`。

## 标准化 JSON 报告（v2）

`results.json` 顶层结构：

- `schema_version`: `llm-eval-report/v2`
- `run`: run_id、时间、状态
- `config`: 模型、引擎、数据集路径
- `summary`: 综合分、`by_category`、`by_difficulty`、`by_sampling_strategy`、`by_suite`
- `results`: 逐条明细（含 difficulty、sampling_strategy）

## 目录结构

```
llm-eval/
├── run_eval.py          # CLI 入口（推荐）
├── eval_engine.py       # 评测引擎核心
├── config.py            # EvalConfig 配置类
├── objective_checks.py  # 客观规则校验
├── deepeval_metrics.py  # DeepEval 指标封装
├── eval_dataset.json    # 26 条分层评测集（single / rag / multi_turn）
├── requirements.txt
└── tests/
    └── test_smoke.py
```

## 面试亮点（可讲）

1. **双引擎评测**：日常 `simple` 快速反馈，发版前 `deepeval` 专业指标。
2. **客观 + 主观融合**：规则校验压制 LLM 裁判分数通胀；要点别名与质量兜底减少中文/RAG/多轮场景的误伤。
3. **评分校准**：跳过不适用的 DeepEval 指标（噪声 RAG 的 contextual_relevancy、纠错多轮的 KR），并对 AR/KR 做条件兜底。
4. **可观测性**：Langfuse Trace 层级清晰，支持按 `run_id` 检索。
5. **工程化**：`EvalConfig` 统一配置、CLI/API 双入口、`--fail-under` CI 门禁、schema v2 标准化报告。
6. **数据集方法论**：难度分层 + 对抗样本 + Prompt 注入/多轮记忆 + 真实场景采样，报告按维度聚合。

## 安全提示

- 切勿将 `.env`（含 API Key）提交到 Git；已提供 `.env.example`。
- 若密钥曾泄露，请在各平台轮换。
