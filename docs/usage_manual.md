# 使用手册 — saudi-ad-agent

> 沙特电商广告创意 LangGraph Agent。本手册聚焦"如何运行 / 如何配置 / 如何扩展"，架构与设计动机见 [README](../README.md) 和 [architecture.md](architecture.md)。

---

## 1. 环境要求

| 项 | 要求 |
| --- | --- |
| Python | **3.11+**（langgraph ≥ 0.2.40 要求） |
| 包管理器 | 任选其一：`uv`（推荐）、`pip + venv`、`poetry` |
| 网络 | 可选 — 不联网时整条流水线走 mock |
| API Key | 可选 — `ANTHROPIC_API_KEY` 未设则 LLM 节点用确定性桩 |

---

## 2. 首次安装

### 用 uv（推荐，零依赖管理 Python 版本）

```bash
cd GaryProject/saudi-ad-agent
uv venv --python 3.11 .venv
uv pip install -r requirements.txt --python .venv/bin/python
```

### 用 pip

```bash
cd GaryProject/saudi-ad-agent
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## 3. 运行

### 3.1 默认 brief（最快验证）

```bash
.venv/bin/python main.py
```

跑预置的 Ajwa dates / Ramadan brief，rich 表格渲染到终端。

### 3.2 自定义 brief

```bash
# 行内
.venv/bin/python main.py --brief "Launch our new modest-fashion summer line for KSA."

# 文件
.venv/bin/python main.py --brief-file my_brief.txt
```

### 3.3 全部 CLI 参数

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--brief` | — | 行内 brief 文本 |
| `--brief-file` | — | brief 文件路径（与 `--brief` 二选一，若都给以 `--brief-file` 优先） |
| `--locale` | `ar-SA` | 目标地区 / 语言 |
| `--audience` | `Saudi adults 25-45, parents, urban` | 目标受众 |
| `--brand-doc` | `data/brand_manual.md` | 品牌手册路径，支持 `.md` 或 `.pdf` |
| `--json` | off | 仅输出 JSON（适合管道接入） |

### 3.4 退出码

| 退出码 | 含义 |
| --- | --- |
| `0` | `eval_status == "pass"` |
| `1` | `eval_status != "pass"`（CTR 低于阈值或品牌安全自检不过） |

可在 CI 里直接用退出码做闸口。

---

## 4. 输出产物

每次运行在 `outputs/runs/<run_id>/` 下生成两份文件：

```
outputs/runs/20260508-152024-331c9d/
├── run.json         # 完整 AgentState（机器可读）
└── storyboard.md    # 人读 storyboard + 资源 URL + eval 摘要
```

`run_id` 形如 `YYYYMMDD-HHMMSS-<6 位 hex>`，按时间排序。

### `run.json` 关键字段（节选）

```jsonc
{
  "storyboard": {
    "hook": "...",
    "body": "...",
    "cta": "...",
    "visual_prompt": "...",          // 喂给 Seedream
    "motion_prompt": "...",          // 喂给 Seedance
    "voiceover": "أهلاً بكم...",      // 喂给 Seed Speech
    "voice": "ar-SA-female-warm"
  },
  "image_url": "https://...",
  "video_url": "https://...",
  "audio_url": "https://...",
  "ctr_estimate": 0.039,             // 0.0 - 1.0
  "eval_status": "pass",             // pass | fail
  "guardrail_status": "pass",
  "guardrail_revision_count": 0,
  "log": [/* 每个节点一条结构化日志 */]
}
```

---

## 5. 配置

复制 `.env.example` 为 `.env`：

```bash
cp .env.example .env
```

| 变量 | 默认 | 含义 |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | 空 | 留空则 Planner / Guardrail-judge / Eval-judge 走 mock；填上则真实调 Claude |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-6` | 模型 ID |
| `SEEDREAM_MOCK` | `1` | `1` 用 mock，`0` 调真实（**真实客户端尚未实现**） |
| `SEEDANCE_MOCK` | `1` | 同上 |
| `SEED_SPEECH_MOCK` | `1` | 同上 |

> **当前 `*_MOCK=0` 不可用**：`src/tools/seed_apis.py` 只实现了 mock 路径。改为真实客户端时把对应函数补全即可，签名已就位。

---

## 6. 流水线节点 — 一句话速查

```
START → rag → planner → guardrail ──pass──→ tool_use → eval → END
                  ▲           │
                  └──fail─────┘  (≤ 2 次 replan)
```

| 节点 | 文件 | 职责 |
| --- | --- | --- |
| `rag` | `src/nodes/rag.py` | 加载 `--brand-doc`，抽出条目级品牌约束塞进 state |
| `planner` | `src/nodes/planner.py` | brief + 品牌约束 → Storyboard JSON |
| `guardrail` | `src/nodes/guardrail.py` | AR/EN 关键词黑名单 + LLM judge；fail 则回 planner，最多 2 次 |
| `tool_use` | `src/nodes/tool_use.py` | Seedream（图）→ Seedance（视频）→ Seed Speech（阿语 TTS），顺序调用 |
| `eval` | `src/nodes/eval.py` | 启发式 CTR + LLM judge CTR 加权混合 + 品牌安全自检 |

---

## 7. 测试

```bash
.venv/bin/pytest -v tests/             # 全套 smoke
.venv/bin/pytest tests/test_smoke.py::test_happy_path_runs_end_to_end -v   # 仅 happy-path
.venv/bin/pytest --durations=10 tests/                                     # 看耗时排序
```

详细结果见 [test_report.md](test_report.md)。

---

## 8. 扩展指引

### 8.1 替换品牌手册

把客户的 `.md` 或 `.pdf` 放进 `data/`，运行时加 `--brand-doc data/<file>`。`rag_node` 走 markdown 时按行抽取，走 pdf 时用 `pypdf` 全文抽取。

### 8.2 新增工具

仿照 `src/tools/seed_apis.py` 的结构：
1. 在 `src/tools/` 下加客户端模块（先写 mock，再写 real）。
2. 在 `src/nodes/tool_use.py` 中加调用并写回 state（建议同时更新 `src/state.py` 的 `AgentState` 字段以保持类型可见）。
3. 加 mock 开关到 `.env.example`。

### 8.3 新增节点

1. 在 `src/nodes/` 加节点函数 `(state) -> partial_state`。
2. 在 `src/graph.py` 用 `g.add_node(...)` + `g.add_edge(...)` 接入；条件分支用 `g.add_conditional_edges`，参考已有的 `guardrail_router`。
3. 在 `src/state.py` 给该节点的输入/输出加字段。

### 8.4 切真实生产模式

1. `.env` 填 `ANTHROPIC_API_KEY` —— Planner / Guardrail-judge / Eval-judge 自动改走真实 Claude。
2. 实现 `src/tools/seed_apis.py` 的真实客户端，把 `SEEDREAM_MOCK` / `SEEDANCE_MOCK` / `SEED_SPEECH_MOCK` 都设为 `0`。
3. 接 distribution / attribution 节点（README §6 已规划）。

---

## 9. 常见问题

**Q: 跑 `python main.py` 报 langgraph ImportError？**
A: 多半是 Python 版本太老。本项目要求 3.11+，系统自带 3.9 装上 langgraph 也跑不起来。用 `uv venv --python 3.11 .venv` 或 `pyenv install 3.11`。

**Q: CTR 永远是 3.90% 或某个固定值？**
A: 离线 mock 模式下 LLM judge 返回确定性桩，CTR 看起来"很稳"是预期行为。设 `ANTHROPIC_API_KEY` 后才是混合估计。

**Q: 看到 `LangChainPendingDeprecationWarning`？**
A: langgraph 上游 forward-deprecation 提示，不影响本项目运行。后续升级 langgraph 时再处理。

**Q: 我想把模型换成 Opus / Haiku？**
A: 改 `.env` 里的 `ANTHROPIC_MODEL`。注意 Opus 4.7 / Sonnet 4.6 / Haiku 4.5 是当前最新一代。

**Q: 想看 LangGraph 内部决策？**
A: `run.json` 的 `log` 字段已记录每个节点的状态、违规命中、工具调用细节。把 `--json` 输出落盘后用 `jq` 探查即可。
