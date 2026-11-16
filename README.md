# tzgate / 时界核验台

把「时间字段是什么语义、落在哪个时区、夏令时怎么处理、分区日期按哪条时间线切」收敛为声明式契约，对本地湖表目录做确定性核验，输出可解释、可复现、可归档的报告与台账。

## 5 分钟跑通

```sh
python -m venv .venv
# Linux/macOS: . .venv/bin/activate
# Windows:     .venv\Scripts\activate
pip install -r requirements-dev.txt

make demo        # fixtures 校验 -> 核验 -> 报告 -> 台账 -> 打印摘要
make test        # 全部测试
```

没有 `make` 时执行等价命令：

```sh
python tools/generate_fixtures.py --check
python -m tzgate verify --contract contracts/default.yaml --root . --out out/demo --run-id demo-001
python -m tzgate summary --out out/demo
python -m pytest
```

仓库已提交合成 fixture；`make demo` 只读取它们，不会重建或修改 `fixtures/lake/**`。演示数据会稳定产出 **13 条问题（11 error / 2 warning）**，覆盖三类目标问题。

## 它回答的问题

- `2024-03-10 02:30:00` 在纽约不存在：本地墙钟落入夏令时前拨空洞。
- `2024-11-03 01:30:00` 在纽约出现两次：缺少偏移量，无法唯一消歧。
- `dt=2024-03-09` 的分区里放了一条 UTC 记录，换算到柏林已是 `03-10 00:30`。
- 同一列号称都是东八区，实际混入 `+00:00` 与 `+09:00` 来源，跨源合并后才发现。

## 不是什么

不是湖表版本账本、不是小文件合并/孤儿文件维护、不是冷热分层、不是入湖水位与迟到审计、不是模式演进闸、不是分区裁剪统计、不是 SCD2 回放、不是 ETL 调度器或通用数据质量规则引擎。它只判断时间线语义：时区归属、偏移消歧、夏令时边界、分区日期与本地日期的一致性；不判断业务值，不写回、不修复。

## 三条核验线

| 代码 | 含义 | 级别 |
| --- | --- | --- |
| `TZ-PART-001` | 记录按声明时区换算的本地日期 ≠ 分区日期 | error |
| `TZ-DST-GAP` | 本地墙钟落入夏令时前拨空洞 | error |
| `TZ-DST-AMBIG` | 本地墙钟落入回拨窗口，缺少偏移量 | error |
| `TZ-OFF-MISMATCH` | 显式偏移 ≠ 规则簿在该时刻的偏移 | error |
| `TZ-OFF-DRIFT` | 同一来源混用多个偏移量（汇总告警） | warning |
| `TZ-PART-LAG-001` | 到达日分区路线：所有候选事件时刻的到达滞后均超限 | error |
| `TZ-PART-LAG-002` | 到达日分区路线：候选滞后部分合规，无法唯一判定 | warning |
| `TZ-LAG-LATE` / `TZ-LAG-INDETERMINATE` | 事件日分区路线：分区正确但滞后超限/无法唯一判定 | warning |
| `TZ-LAG-ORDER` | 到达时刻早于所有候选事件时刻，时钟顺序无效 | warning |
| `TZ-PARSE-001` / `TZ-SHAPE-*` / `TZ-IO-001` | 解析、列结构或读取问题 | error |

每条证据精确到文件、行号、字段与原始文本；issue id 由证据内容哈希得到。

## 契约

`contracts/default.yaml` 声明数据集、分区模式与时间语义。每个数据集支持三种语义：

- `local_wall`：无偏移的本地墙钟时间，检查 DST 空洞/回拨；
- `utc_instant`：必须带 `Z` 或显式偏移，换算为声明时区的本地日期再对分区；
- `offset_aware`：显式偏移必须匹配规则簿，并按来源检查偏移漂移。

```yaml
datasets:
  - name: events_utc
    root: fixtures/lake/events_utc
    partition: {pattern: "dt={date}", date_format: "%Y-%m-%d", basis: zone_local_date}
    file_glob: "part-*.csv"
    time:
      column: event_time
      format: iso8601
      semantics: utc_instant
      zone: Europe/Berlin
    source: {column: source_id}
    checks: {partition: true, dst: false, offset: false}
```

路径必须相对 `--root`，禁止绝对路径、`..`、反斜杠与符号链接逃逸；未知键会直接拒绝，避免静默拼写错误。

### 双时钟：事件时间与到达时间

当同一行的事件发生时刻与落湖到达时刻不同，可以声明两个时间列。`partition.basis` 必须显式固定为 `event_local_date`（事件本地日）或 `arrival_local_date`（到达本地日），不能沿用 `zone_local_date`：

```yaml
datasets:
  - name: late_orders
    root: fixtures/dual_lake/cross_day
    partition: {pattern: "dt={date}", date_format: "%Y-%m-%d", basis: event_local_date}
    time:
      event: {column: event_time, format: iso8601, semantics: local_wall, zone: America/New_York}
      arrival: {column: arrival_time, format: iso8601, semantics: offset_aware, zone: America/New_York}
      max_arrival_lag: 2h
    source: {column: source_id}
```

- `max_arrival_lag` 支持 `30s` / `90m` / `2h` / `1d` / `00:30`，必须为正。
- 同一条跨午夜晚到记录，在 `event_local_date` 下落到到达日目录是分区错误；在 `arrival_local_date` 下、滞后未超限则合规。两条路线都合理，契约固定选择，`report.json` 的 `partition_routes`、`summary.md` 与 SQLite `dataset_clocks` 台账都会写明依据。
- 回拨窗口（如纽约 `2024-11-03 01:30`）有两个候选瞬时。引擎对每个候选分别推导到达滞后，给出“全部合规 / 全部超时 / 无法唯一判定”三种结论，证据列出两个候选与各自滞后，不会任选候选后报告精确滞后。
- 前拨空洞、到达早于所有候选、列缺失或解析失败分别报 `TZ-DST-GAP`、`TZ-LAG-ORDER`、`TZ-SHAPE-*`、`TZ-PARSE-001`，不会被误报为分区错位。

示例契约：`contracts/dual_event.yaml`（事件日路线）与 `contracts/dual_arrival.yaml`（到达日路线），隔离 fixture 位于 `fixtures/dual_lake/`，与默认演示数据完全分离。

## 规则簿：不依赖系统 tzdata

`contracts/timezone_rules.yaml` 用「基础偏移 + 严格递增的 UTC transition」声明时区。引擎自己做 UTC→本地、本地→候选瞬时、空洞/回拨窗口推导，因此跨机器、跨 tzdata 版本结果一致。内置 2024 年的 `America/New_York`、`Europe/Berlin`、`Asia/Shanghai`、`Australia/Sydney`、`UTC`；扩展到其他年份只需追加 YAML。

## 输出

写入 `--out`（默认 `out/demo`）的五个文件：

| 文件 | 用途 |
| --- | --- |
| `report.json` | 完整结构化报告，含契约/规则簿 sha256 与产物哈希 |
| `summary.md` | 可评审、可归档的中文摘要 |
| `issues.csv` | 一行一条证据，便于 diff 与表格处理 |
| `report.html` | 自包含 HTML，内联 CSS/JS，无外部资源 |
| `ledger.sqlite3` | 只读复核台使用的 SQLite 台账（双时钟运行额外含 `dataset_clocks` 表） |

退出码默认 0（发现问题仍视为核验完成）；`--fail-on warning|error` 可在 CI 中变为 1。用法错误返回 2。

## 确定性与只读

- 同一输入、契约、`--run-id` 下，`report.json`、`summary.md`、`issues.csv`、`report.html`、`ledger.sqlite3` 逐字节一致；测试用不同 `PYTHONHASHSEED` 交叉验证。
- 报告不写墙钟时间、PID、随机数、绝对路径或 dict/set 遍历顺序噪声；所有集合输出前显式排序。
- 可选 `--generated-at` 注入生成时间；不传则完全省略。
- 输入目录只读；输出路径若落在 `fixtures/lake` 或任何数据集根目录内会被拒绝。
- 运行期零网络请求；HTML 不引用 CDN、外部字体或远程图片。

## 本地复核台

```sh
python -m tzgate console --out out/demo --port 0
```

服务硬编码绑定 `127.0.0.1`，端口由系统分配并打印；只读打开 SQLite（`mode=ro&immutable=1`），提供概览、筛选、问题详情与 `/api/issues` JSON。没有 host 参数，无法配置成 `0.0.0.0`。

## 离线安装

运行时仅依赖 `PyYAML`，开发测试仅额外依赖 `pytest`。在联网机器准备 wheel：

```sh
pip download -r requirements-dev.txt -d wheelhouse
```

把仓库与 `wheelhouse/` 一起拷到离线机器：

```sh
python -m venv .venv
. .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install --no-index --find-links wheelhouse -r requirements-dev.txt
python -m pytest
```

若目标机器已预装 PyYAML 与 pytest，直接 `python -m tzgate verify ...` 即可，不需要安装步骤；程序本身不发任何网络请求。

## 测试与规模

`python -m pytest` 覆盖规则簿换算、空洞/回拨、三类检测、报告转义、SQLite 台账、只读复核台、fixture 重生成、字节级确定性、`PYTHONHASHSEED` 变异、输入只读与输出路径防护。每个 Python 源文件 ≤ 300 行；全部 Python 代码约 4.1k 行（含测试与 fixture 生成器）。

## 目录

`tzgate/` 核验引擎、规则簿、报告、台账、只读复核台；`contracts/` 默认契约与时区规则簿；`fixtures/lake/` 合成湖目录（只读输入）；`tools/` fixture 生成/校验脚本；`tests/` 测试；`Makefile` 演示与测试入口。

## 边界与限制

规则簿是显式数据，不覆盖历史或未来年份；使用前应声明所需区间并扩展 YAML。`local_wall` 的分区日界天然等于墙钟日期，因此该语义主要检测 DST 空洞/回拨；跨日错分主要出现在 `utc_instant` 与 `offset_aware`。本项目不做数据修复，只给出可复核证据。
