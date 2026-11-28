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
python3 -m unittest discover -s tests -v
```

## 本地电子送达工作台（`workbench`）

工作台在来源记录之上回答三类问题：某份文书究竟送达给谁、这次签收能不能作为起算依据、期限台已有的起算决定是否需要复核。全部判定只依据本地数据，可用 `--through <seq>` 重放任意到达时点。

### 规则口径

1. **登录身份须核准。** 登录主体只有在 `identity` 关联 `verified=true` 时才能代表登记机关命名范围内的具体人员；未核准时不暴露任何待签收件或他人收件细节。
2. **代理许可按人、案件、动作、生效区间核对。** 同一登记 `ref` 的更正以较新的完整修订整体替换旧修订；多份单项许可的动作取并集。撤销或“自某时起无效”的更正通过新区间生效，区间不覆盖的时刻即无该许可。
3. **动作含义。** `open` 可查看；`ack` 可签收正式文书并起算；`supply` 只能补交材料，不产生查看或签收权。普通通知（ordinary）有 `open` 即可签收；正式文书（formal，涉隐私）需要单独准许的 `ack`。
4. **本人确认优先。** `personal_ack=true` 的文书只能由当事人本人签收，代理人即使获准读取也不能替本人起算。代理撤销后，当事人本人的收件权始终有效。
5. **多条收件途径不重复起算。** 普通通知可同时保留两条途径；分开或新增途径不会让同一份文书修订起算两次，起算只取最早的合法签收。
6. **签收效力按签收当时的权利判断。** 以收件端确认的实际签收时刻（`acknowledged.occurred`）代入当前已知的最新登记修订。合法旧签收保留证明力；晚到的无权签收不能让期限开始，记录留痕但无效。
7. **登记更正与迟到签收相遇。** 若期限台已依据后来被认定无权的签收作出起算决定，工作台把该决定标为需复核，暂停依赖它的期限办理，同时保留原签收与更正依据，待重新依法送达或登记复核明确后才采用新依据；同案其他有效签收与决定不受影响。
8. **修订严格隔离。** 重发若换了修订（rev），旧件的已读/签收记录留在旧修订上，不迁移到新件；新修订另行核对收件权。
9. **待确认与无签收可辨。** `pending` 是回执尚未确认，`absent` 是收件端明确没有签收；等候期间再次发件不会制造第二个有效起算。

### 命令

```bash
# 按文书修订汇总送达状态、当下能否起算、哪些旧决定有争议
python3 -m workbench.cli documents [--case case-81] [--json]

# 单份文书的完整轨迹；--all-revs 同时列出各修订，--rev 指定单一修订
python3 -m workbench.cli trace n-8 --all-revs
python3 -m workbench.cli trace n-8 --rev 1

# 期限台起算决定复核（--only-review 只看需暂停项）
python3 -m workbench.cli decisions [--only-review] [--json]

# 当事人视角的收件箱：当前可查看、待确认、明确无签收
python3 -m workbench.cli inbox portal:91 [--now 30] [--json]

# 送达员受理新文书、新尝试或委托/身份变更（seq 自动顺延，写入 local/）
python3 -m workbench.cli seq
python3 -m workbench.cli submit documents '{"notice":"n-11","rev":1,"case":"case-81","holder":"A:27","kind":"ordinary","personal_ack":false}'
python3 -m workbench.cli submit delivery '{"attempt":"a6","notice":"n-11","rev":1,"principal":"portal:91","result":"offered","occurred":31}'
python3 -m workbench.cli submit mandates '{"ref":"B:m6","rev":2,"person":"B:41","holder":"A:27","case":"case-81","actions":["open"],"from":27,"until":35}'

# 重放到达时点：例如只看 seq 17 之前（a1 仍待确认、决定尚未作出）
python3 -m workbench.cli --through 17 documents
```

`local/` 是送达员的本地工作目录，结构与 `sources/` 同构且已被 git 忽略；工作台自动合并两边记录后按 `seq` 排序。所有查询命令都支持 `--json` 输出结构化结果。

### 代码结构

| 模块 | 职责 |
| --- | --- |
| `workbench/model.py` | 人员编号、生效区间及各来源的领域对象 |
| `workbench/books.py` | 来源合并装载、登录身份关联册、代理许可修订册 |
| `workbench/rules.py` | 具体时刻对具体文书修订的查看权/签收权规则 |
| `workbench/engine.py` | 尝试聚合、签收判定、唯一起算依据、决定复核、个人收件箱 |
| `workbench/store.py` | 本地记录的校验与追加（全局 seq 顺延） |
| `workbench/cli.py` | 命令行工作台，中文展示与 JSON 双输出 |
