# NetOptClaw 无线网络优化 Skill 调度系统

NetOptClaw 是一个零依赖演示版的“类 OpenClaw”无线网优智能体系统。它把工单查询、KPI 获取、告警关联、根因分析、方案生成、风险守卫、模拟下发和效果评估拆成原子 Skill，再由轻量 Planner 与 DAG Executor 自动编排。

## 快速启动

```bash
python3 scripts/seed_data.py
python3 server.py
```

然后打开：

```text
http://127.0.0.1:8000
```

如果 8000 端口被占用，可以改用：

```bash
python3 server.py --port 8010
```

> 该版本只使用 Python 标准库、SQLite、原生 HTML/CSS/JS，不需要下载 OpenClaw，也不需要安装 npm 或 Python 第三方包。

## 项目结构

```text
netopt/
  database.py        SQLite 初始化与 JSON 编码
  seed.py            mock 网优数据生成
  planner.py         自然语言意图识别与 Skill 编排
  skill_runtime.py   Skill、Registry、DAG Executor
  skills.py          网优原子 Skill 实现
  orchestrator.py    对外调度入口
web/
  index.html         网优指挥舱
  styles.css         深色科技风 UI
  app.js             浏览器端 API 调用与渲染
scripts/
  seed_data.py       一键造数
tests/
  test_netopt.py     基础回归测试
server.py            标准库 HTTP API + 静态文件服务
```

## 已实现 Skill

- `ticket_query`：查询小区工单、投诉与派单状态
- `cell_profile`：查询小区画像、参数和邻区关系
- `kpi_fetch`：获取最近 KPI 趋势与统计摘要
- `alarm_correlate`：关联告警并判断告警影响
- `coverage_analysis`：分析弱覆盖、越区覆盖和覆盖失衡
- `interference_analysis`：分析上行干扰、邻区干扰和 PCI/MOD3 冲突
- `capacity_analysis`：分析 PRB、用户数和吞吐容量瓶颈
- `handover_analysis`：分析切换失败、邻区漏配和参数异常
- `root_cause_ranker`：融合多证据生成根因 TopN
- `solution_generator`：生成无线优化方案和执行动作
- `risk_guard`：进行方案风险校验和审批判断
- `plan_dispatch`：模拟方案下发并记录执行日志
- `effect_evaluator`：对比 KPI 并输出效果评估
- `report_writer`：汇总为网优工程师可读报告

## 演示输入

```text
查询高科路_001小区的工单信息，分析根因，并生成优化方案
```

预期链路：

```text
ticket_query -> cell_profile -> kpi_fetch -> alarm_correlate -> coverage_analysis
-> interference_analysis -> capacity_analysis -> handover_analysis -> root_cause_ranker
-> solution_generator -> risk_guard -> report_writer
```

```text
世纪大道_005小区最近投诉多，判断是弱覆盖还是干扰，并给出处理建议
```

```text
对张江_012小区生成优化方案，但不要下发，先做风险评估
```

```text
请下发陆家嘴_003小区的容量优化方案并进入效果观察
```

```text
评估昨天对陆家嘴_003小区下发方案后的效果
```

## API

- `GET /api/health`
- `GET /api/skills`
- `GET /api/cells`
- `GET /api/tickets?cell_id=CELL-001`
- `POST /api/chat`
- `POST /api/dispatch/{plan_id}`
- `GET /api/traces/{trace_id}`
- `GET /api/evaluation/{cell_id}`
- `GET /api/kpi/{cell_id}?hours=24`

`POST /api/chat` 示例：

```bash
curl -s http://127.0.0.1:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"query":"查询高科路_001小区的工单信息，分析根因，并生成优化方案"}'
```

## 测试

```bash
python3 -m unittest discover -s tests
```

## 安全边界

- 所有 Skill 均为白名单注册
- Skill 不能执行任意 shell 命令
- 所有执行步骤写入 `traces` 与 `skill_runs`
- 方案下发默认为模拟下发
- 高风险方案要求人工审批
- LLM 暂未接入，当前根因与方案为规则引擎版本，便于演示和审计
