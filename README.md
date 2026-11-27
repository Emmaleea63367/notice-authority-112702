# 正式文书的身份签收与起算

送达员核对电子文书与代理登记，收件端和期限台各自提供受理或查询记录。登记机关的人员键以机关号和本地人员号共同定位。纸面委托及收件端的记录以不同时间提供，不假设同时送到。

资料中的对象、身份及记录均为虚构。各来源使用 UTF-8 JSON Lines，一行是一个原始对象。`seq` 是全局到达序号，按演练资料的可知顺序排列。事实发生时刻、确认时刻与适用时段以同行中的独立字段为准，用相对演练起点的整数分钟表达。JSON `null` 保留来源的缺省或未确认值。除非来源明确使用相同标识，编号只在所属来源和实体域内有意义。

## 来源

- `sources/identity.jsonl`　登录核验方的人员关联，principal 为登录主体，person 为登记机关与人员号，verified 表示关联已核。
- `sources/mandates.jsonl`　代理登记的完整单项许可修订，person 对应代理人，holder 对应当事人。from/until 为左闭右开适用区间，actions 可在不同登记行分别出现。
- `sources/documents.jsonl`　办案台提供的文书与送达类别，notice/rev 标识文书版本，personal_ack 表示必须由持有人本人签收。
- `sources/delivery.jsonl`　收件端的状态记录，attempt 是本端发件标识，occurred 是本端确认动作的演练分钟，pending 和 absent 分别是尚未确认和明确无签收。
- `sources/docket.jsonl`　期限台已有的外部决定记录，basis 指向收件端尝试，accepted_on 为当时实际采纳时间。

## 查看记录

使用 Python 3.11.2 标准库，无需安装第三方包。以下命令列出可读取的来源、查看单个来源记录并检查资料格式。读取命令按输入顺序输出 JSON，可通过 `--through` 限制已到达的记录。

```bash
python3 source_records.py
python3 source_records.py identity
python3 -m unittest discover -v
```
