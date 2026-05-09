# 测试报告 — saudi-ad-agent

> 项目路径：`GaryProject/saudi-ad-agent`
> 报告生成时间：2026-05-08
> 执行人：Claude Code

---

## 1. 测试环境

| 项 | 值 |
| --- | --- |
| 操作系统 | macOS (Darwin 25.4.0) |
| Python | 3.11.14（通过 `uv` 管理；系统自带 3.9.6 不满足 langgraph ≥ 0.2.40 要求） |
| 虚拟环境 | `GaryProject/saudi-ad-agent/.venv` |
| 测试框架 | pytest 9.0.3，pluggy 1.6.0 |
| 关键依赖 | langgraph 0.2.40+、langchain-core 0.3、anthropic 0.40+、pydantic 2、pypdf 6.10、rich 15、python-dotenv 1.2 |
| 运行模式 | **OFFLINE-MOCK**（未设置 `ANTHROPIC_API_KEY`，所有 LLM 与 Seed* 调用走确定性桩） |

---

## 2. 用例清单与结果

文件：`tests/test_smoke.py`

| # | 用例 | 类型 | 关注点 | 结果 | 耗时 |
| --- | --- | --- | --- | --- | --- |
| 1 | `test_happy_path_runs_end_to_end` | 集成（端到端） | LangGraph 5 个节点全跑通；storyboard / image_url / video_url / audio_url / ctr_estimate 均落位；log 至少 5 条 | ✅ PASS | 0.20s |
| 2 | `test_guardrail_catches_alcohol_in_storyboard` | 单元 | 关键词扫描捕获 `wine` / `beer` / `champagne` / 阿拉伯文 `خمر` | ✅ PASS | < 0.005s |
| 3 | `test_guardrail_passes_clean_storyboard` | 单元 | Ramadan 模式下干净文案不被误杀 | ✅ PASS | < 0.005s |

**统计：**

```
3 passed, 0 failed, 0 skipped, 1 warning  (总耗时 ≈ 0.30s, 进程 wall ≈ 0.87s)
```

---

## 3. 端到端运行验证（`python main.py --json`）

为确认产品化路径无误，额外执行了一次 CLI 默认 brief（Ajwa dates / Ramadan）。

**关键输出：**

| 字段 | 值 |
| --- | --- |
| `eval_status` | `pass` |
| `guardrail_status` | `pass`（revisions = 0） |
| `ctr_estimate` | 0.039（即 3.90%） |
| `image_url` | `https://mock.seedream.bytedance.com/img/...png` |
| `video_url` | `https://mock.seedance.bytedance.com/vid/...mp4`（6.0s, 24fps, 1080x1920） |
| `audio_url` | `https://mock.seedspeech.bytedance.com/tts/...mp3`（voice=`ar-SA-female-warm`, 4.2s） |
| `log` 节点数 | rag → planner → guardrail → tool_use(3 calls) → eval |
| 进程 wall | ≈ 0.71s |

**Mock 延迟模拟：** Seedream 820ms / Seedance 4200ms / Seed Speech 380ms。注意这些是 mock 客户端在结果里附带的标注值，而非真实等待时间——离线模式下整条流水线实际跑完不到 1s。

---

## 4. 警告

```
LangChainPendingDeprecationWarning:
  The default value of `allowed_objects` will change in a future version.
  Pass an explicit value (e.g., allowed_objects='messages' or allowed_objects='core').
```

来自 `langgraph/cache/base/__init__.py:8`，是 langgraph 内部的 forward-deprecation 提示，**不影响本项目代码与测试结果**。后续升级 langgraph 版本时再观察是否需要在 `build_graph()` 中显式传参。

---

## 5. 覆盖范围分析

| 维度 | 覆盖情况 |
| --- | --- |
| 节点：rag | ✅（happy-path 间接覆盖：`rules_loaded=14` 来自 `data/brand_manual.md`） |
| 节点：planner | ✅（happy-path） |
| 节点：guardrail | ✅（happy-path + 两条专用单元用例：违规命中 / 合规放行） |
| 节点：tool_use | ✅（happy-path 三次工具调用全部完成） |
| 节点：eval | ✅（happy-path，ctr_estimate ∈ (0, 1) 边界检查） |
| guardrail 重试回路（planner ⇄ guardrail） | ⚠️ 未直接验证（happy-path 一次通过，未触发 replan 分支） |
| LLM 真实调用路径（`ANTHROPIC_API_KEY` 已设） | ⚠️ 未覆盖（CI 默认 offline-mock，符合项目"无密钥可跑"目标） |
| Seed* 真实客户端（`*_MOCK=0`） | ⚠️ 未实现，无法测试 |
| PDF 品牌手册路径（`brand_doc_path` 为 .pdf） | ⚠️ 仅 markdown 路径被覆盖 |

---

## 6. 复现步骤

```bash
cd /Users/moon/workspace/cc/GaryProject/saudi-ad-agent

# 一次性环境初始化
uv venv --python 3.11 .venv
uv pip install -r requirements.txt pytest --python .venv/bin/python

# 跑测试
.venv/bin/pytest -v tests/

# 跑端到端 CLI
.venv/bin/python main.py            # rich 渲染输出
.venv/bin/python main.py --json     # 机器可读
```

每次 CLI 运行的 `run.json` 与 `storyboard.md` 落在 `outputs/runs/<timestamp>/`。

---

## 7. 结论

- 当前提交在离线 mock 模式下**全部测试通过**，端到端流水线 happy-path 可重现。
- 未发现 blocker；唯一一条 warning 来自上游库，非本项目代码问题。
- 若要进一步加固，建议补三条用例：
  1. 构造一个会被 guardrail 拒绝的 brief，断言 `guardrail_revision_count` 至少为 1（覆盖 replan 边）。
  2. 注入 `ANTHROPIC_API_KEY` 的契约测试（带 `pytest.skip` 守卫，仅在密钥存在时跑）。
  3. 用 `data/` 下放一份小 PDF，覆盖 `pypdf` 解析路径。
