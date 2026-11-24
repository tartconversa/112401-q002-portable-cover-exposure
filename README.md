# 多平台接单时段的保障责任与待赔留存

合作保障计划的演练台账使用 UTC+08:00 合同日界。时间统一写成 ISO 8601，时段左闭右开。金额为元。报案流水中的 paid 是此前实际给付事实。

## 资料约定

CSV 使用 UTF-8，首行为列名。多值字段以竖线分隔。空单元格表示来源未填。

- identity.csv。平台分别给出本地代号。person 为演练计划联系人码，version=2 可更正之前的关系，起止适用范围见本行。
  字段为 `platform`、`account`、`person`、`from`、`to`、`version`、`received`。
- activity.csv。平台观测时段，state 是 wait、carry 或 pause，task 指已知运单。event_id 不因补报重用。
  字段为 `event_id`、`platform`、`account`、`zone`、`state`、`start`、`end`、`task`、`received`。
- gaps.csv。收报系统的断档声明，可与已知时段部分重叠。resolved 为补报提交的日期，空白表示未收到。
  字段为 `gap`、`platform`、`account`、`start`、`end`、`resolved`。
- cover.csv。合同月额度及保障范围，state=carry 或 wait。月额度按一个合同计，用同一 cover_id 分列保障状态时不相加。
  字段为 `cover_id`、`month`、`platform`、`zone`、`state`、`limit`、`rate_per_hour`。
- claims.csv。事故联系人的报案及既有给付，state 为当前收到的处理回执状态。paid_cover 仅在已有给付时填写。
  字段为 `claim`、`person`、`zone`、`instant`、`requested`、`received`、`state`、`paid_cover`、`paid`。
- rulings.csv。独立调查裁定，所列事实可反驳先前对活动区间或代号归属的假设。 试点的报案可拆给付，最终分担比例以独立裁定为准。
  字段为 `ruling`、`record`、`accepted_fact`、`received`。

## 检查演练资料

使用 Python 3.11.2 标准库，无需安装第三方依赖。在项目根目录运行下列命令可检查 CSV 读取、资料关联及样例记录完整性。

```bash
python3 -m unittest discover -s tests -v
```

隔离模式（`-I`，不信任当前目录）下运行时，测试引导会自行把项目根加入导入路径：

```bash
python3 -I -m unittest discover -s tests -t . -v
```

## 保障台账（assurance 包）

`assurance/` 把上面的资料约定落成可反复装入月报与报案资料的纯标准库项目。
全部时间按 UTC+08:00 合同日界、左闭右开；金额用 `Decimal`，工时按秒计后四位小数展示。

### 规则到模块的对应

| 规则 | 实现位置 |
| --- | --- |
| 合同日界、跨月切开、ISO 时间 | `assurance/timeutil.py` |
| 左闭右开时段、原子切片 | `assurance/intervals.py` |
| 版本化身份关联、裁定强制版本、适用日期 | `assurance/identity.py`、`basis.py` |
| 认定基准（原月结/裁定后双账重跑） | `assurance/basis.py`、`review.py` |
| 人维度时间轴、挂起压制、断点 unknown、逐切片身份归属 | `assurance/timeline.py` |
| 承运优先、并行平分、等单仅参保平台平分、上下界 | `assurance/exposure.py` |
| 工作区/月/状态/平台回溯、费用归属、双基准差额 | `assurance/settlement.py` |
| 承保边界、责任组合、缺证待核、身份重查 | `assurance/claims.py` |
| 已赔/确认/或然三层额度、排队、释放、逆向更正 | `assurance/ledger.py` |
| 异议、裁定落地、份额转移解释 | `assurance/review.py`、`rulings.py` |
| CSV 导出与控制台核对视图 | `assurance/reporting.py`、`cli.py` |

关键纪律：

- 一份暴露以"至少一个有效平台在线"为准；两个实际任务并行时承运平台平分，
  纯等单时仅在线且参保的平台平分；挂起（pause）不属于在线。
- 未补报断点只产生下界与上界，归属写 `UNDETERMINED` 并记录候选平台，
  绝不把某平台少报解释成另一家的增加。
- 跨月/跨身份边界的长事件逐切片重新认定，同一事件可分属不同联系人，
  但同一人内部各切片份额之和恒等于挂钟时长（测试守恒断言）。
- 额度分 `paid`（既有给付，不可挤占）、`confirmed`（确认预留）、
  `contingent`（互斥或然预留，取最大值不相加）三层；新报案按受理顺序排队。
- 逆向更正固化原 `paid`，差额生成对账项转下期，报案人不会再次变成全额可领。

### 命令行

全局参数 `--data/--month/--out/--as-of` 须放在子命令前。

```bash
# 原月结基准的月报（暴露段、归属、额度、给付计划）
python3 -m assurance.cli --data samples --month 2026-07 --out out/month monthly

# 原月结 vs 裁定后的暴露与责任差额（含 allocation_delta.csv）
python3 -m assurance.cli --data samples --month 2026-07 --out out/cmp compare

# 裁定后基准的报案责任与额度台账
python3 -m assurance.cli --data samples --month 2026-07 --out out/claims claims

# 在原月结基准上确认/排除某报案的或然责任组合
python3 -m assurance.cli --data samples --month 2026-07 --as-of 2026-07-05 \
  --out out/rule rule --baseline --claim K2 --combo U1
python3 -m assurance.cli --data samples --month 2026-07 --as-of 2026-07-05 \
  --out out/rule rule --baseline --claim K2 --release

# 逆向更正历史已赔责任（原给付固化，差额转下期）
python3 -m assurance.cli --data samples --month 2026-07 \
  --out out/correct correct --claim K1 --cover PC7
```

导出文件：`exposure_segments.csv`（逐段证据与候选平台）、`allocation.csv`
（归属与费用上下界）、`claim_liability.csv`（互斥责任组合）、
`cover_availability.csv`（当期真正可再占用额度）、`payment_plan.csv`
（付/预留/排队）、`escalations.csv`（交经办人冲突）、`corrections.csv`
（逆向更正对账项）、`unassigned_events.csv`（无生效关联的上报）。
