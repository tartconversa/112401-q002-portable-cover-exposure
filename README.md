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
