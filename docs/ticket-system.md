# 工单系统（Tickets）设计文档

> 状态：v3 已实现（v1 协作流转 + P3 审批挂接 + v15 服务目录/值班派单） | 维护者：BingOps Team
> 相关代码：`bingops/models/ticket.py`、`bingops/services/ticket_service.py`、`bingops/services/ticket_attachment_service.py`、`bingops/api/v1/tickets.py`、`bingops/services/change_freeze_service.py`、`bingops/core/feishu_bot.py`、`bingops/core/oss_client.py`
> 数据库脚本：`sql/schema.sql`（权威源）+ `sql/migrations/`（增量迁移，最新 v38 工单附件）

## 1. 定位

面向平台所纳管系统（CMDB 资源、K8s 集群、云账号）的运维工单系统，覆盖申请、变更、故障上报等日常运维协作场景。

设计原则：

- 遵循项目三层分层架构（API → Service → Repository → Database）
- 复用现有 RBAC 权限体系、统一响应信封与异常体系
- 工单全生命周期操作全部落流转记录，形成可审计时间线

## 2. 数据模型

### 2.1 tickets（工单主表）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | BIGSERIAL | 主键 |
| ticket_no | VARCHAR(32) UNIQUE | 工单号，格式 `TK-{id:08d}`，创建后由服务层基于主键回填 |
| title | VARCHAR(256) | 标题 |
| description | TEXT | 描述 |
| ticket_type | VARCHAR(32) | 工单类型，当前：`general\|request\|change\|incident` |
| status | VARCHAR(16) | 状态：`open\|in_progress\|resolved\|closed\|cancelled` |
| priority | VARCHAR(16) | 优先级：`low\|medium\|high\|urgent` |
| creator_id | BIGINT → users | 创建人 |
| assignee_id | BIGINT → users | 处理人（可空） |
| related_resource_id | BIGINT → cmdb_resources | 可选关联 CMDB 资源，`ON DELETE SET NULL` |
| resolved_at / closed_at | TIMESTAMPTZ | 解决/关闭时间点 |
| created_at / updated_at | TIMESTAMPTZ | 标准时间戳（updated_at 由触发器维护） |

索引：status、ticket_type、priority、creator_id、assignee_id、created_at DESC。

### 2.2 ticket_comments（流转/评论记录表）

不可变表（仅 created_at，无 BaseMixin，同 `cmdb_change_logs` 惯例）：

| 字段 | 说明 |
|------|------|
| ticket_id | 所属工单，`ON DELETE CASCADE` |
| user_id | 操作人 |
| action | `create` \| `comment` \| `assign` \| `status_change` |
| content | 内容（评论文本 / 状态流转备注 / 创建时的描述快照） |
| from_value / to_value | 指派与状态变更的前后值（如 `open → resolved`） |

作用：工单详情页一次性还原完整时间线，同时是未来审计报表的数据源。

## 3. 状态机

```
open ──→ in_progress ──→ resolved ──→ closed
  │           │              │
  │           └─→ cancelled  └─→ in_progress（重开，自动清空 resolved_at）
  └──────────→ cancelled
```

| 当前状态 | 允许流转目标 |
|----------|--------------|
| open | in_progress, cancelled |
| in_progress | resolved, cancelled |
| resolved | closed, in_progress |
| closed / cancelled | （终态，不可流转，禁止评论） |

服务端按 `STATUS_TRANSITIONS` 矩阵强校验，非法流转返回 `ValidationError`（HTTP 422）。
流转到 `resolved` / `closed` 时自动写入 `resolved_at` / `closed_at`。

## 4. API 设计

前缀：`/api/v1/tickets`，tag：`tickets`。

| 方法 | 路径 | 权限码 | 说明 |
|------|------|--------|------|
| GET | `/tickets` | `ticket:list` | 分页列表；过滤：status/ticket_type/priority/creator_id/assignee_id/keyword |
| POST | `/tickets` | `ticket:create` | 创建工单（201），自动生成工单号 |
| GET | `/tickets/{id}` | `ticket:list` | 详情（含完整流转记录） |
| PUT | `/tickets/{id}` | `ticket:update` | 编辑标题/描述/优先级 |
| DELETE | `/tickets/{id}` | `ticket:delete` | 删除工单 |
| POST | `/tickets/{id}/assign` | `ticket:assign` | 指派/转派处理人 |
| POST | `/tickets/{id}/status` | `ticket:update` | 状态推进（可附备注） |
| GET | `/tickets/{id}/comments` | `ticket:list` | 流转记录列表 |
| POST | `/tickets/{id}/comments` | `ticket:create` | 追加评论（201） |

权限码复用说明：评论是"创建评论记录"复用 `ticket:create`；状态流转属于工单处理复用 `ticket:update`。

## 5. 业务规则

| 规则 | 约束 |
|------|------|
| 编辑/删除 | 仅 `open` 状态，且限创建人或超级管理员 |
| 指派 | 仅 `open` / `in_progress` 状态；处理人必须存在且 `is_active` |
| 评论 | `closed` / `cancelled` 终态禁止评论 |
| 创建校验 | ticket_type / priority 必须在枚举内；assignee_id 用户必须存在；related_resource_id 资源必须存在 |
| 工单号 | 创建时先 flush 取主键，再回填 `TK-{id:08d}`，全局唯一 |
| 响应 | 列表/详情均携带 creator_name / assignee_name（display_name 缺失回退 username） |

## 6. 权限集成

新增权限码（已同步 `schema.sql`、`v4_tickets.sql`、`init_data.py`）：

| 权限码 | 说明 |
|--------|------|
| `ticket:list` | 查看工单（列表/详情/评论） |
| `ticket:create` | 创建工单、添加评论 |
| `ticket:update` | 编辑工单、状态流转 |
| `ticket:assign` | 指派/转派 |
| `ticket:delete` | 删除工单 |

预置角色行为：admin 全量；operator 全量（可提单可处理）；viewer 仅 `ticket:list` 只读；auditor 同 viewer。

## 7. 三层实现清单

| 层 | 文件 | 要点 |
|----|------|------|
| Model | `bingops/models/ticket.py` | `Ticket`（BaseMixin）+ `TicketComment`（不可变表），relationship 预定义 creator/assignee |
| Schema | `bingops/schemas/ticket.py` | Create/Update/Assign/Status/CommentCreate + Response/DetailResponse |
| Repository | `bingops/repositories/ticket_repo.py` | `TicketRepo`（多条件分页）+ `TicketCommentRepo`，`selectinload` 预加载用户关系 |
| Service | `bingops/services/ticket_service.py` | 状态矩阵、枚举校验、工单号生成、操作人身份判定 |
| API | `bingops/api/v1/tickets.py` | 9 个端点，路由已在 `main.py` 注册 |

## 8. 部署步骤

```bash
# 存量库增量迁移
psql -U <user> -d <dbname> -f sql/migrations/v4_tickets.sql
# 全新环境直接执行 schema.sql（已包含工单表）
psql -U <user> -d <dbname> -f sql/schema.sql
```

迁移脚本自带幂等（`IF NOT EXISTS` + `ON CONFLICT DO NOTHING`），admin 角色自动获得全部 ticket 权限。

---

## 9. SRE 工单类型扩展分析（待决策）

当前为 4 类通用型（`general/request/change/incident`）。结合 SRE 值班与运维流程，可细化为 9 类：

| code | 名称 | 典型场景 | 特殊流程诉求 |
|------|------|----------|--------------|
| `request` | 资源申请 | 服务器/云资源开通、扩容、数据库实例 | 审批流，关联 CMDB 资源 |
| `change` | 变更发布 | 版本发布、配置/DB/网络变更 | 变更窗口、回滚预案、审批 |
| `incident` | 故障处置 | 告警升级、服务中断、性能劣化 | 优先级偏高，需 SLA 计时 |
| `access` | 权限申请 | 账号开通、堡垒机/生产访问、数据权限 | 审批 + 有效期 |
| `troubleshooting` | 问题排查 | 日志捞取、链路诊断、疑难协助 | 协作型而非审批型 |
| `security` | 安全事件 | 漏洞处置、入侵响应、泄露应急 | 保密性、可见范围受限 |
| `data_ops` | 数据操作 | 备份恢复、数据订正、清理、导出 | 高危操作，强审批 |
| `maintenance` | 维护窗口 | 停机维护申请与通告 | 排期、广播通知 |
| `general` | 通用 | 咨询、杂项兜底 | - |

**落地成本**：`ticket_type` 为 `VARCHAR(32)`，枚举校验在服务层 `VALID_TICKET_TYPES` 元组，扩充仅需修改常量与 Schema 描述，无数据库迁移。

## 10. 后续规划（候选，未实现）

- SLA 计时与超时预警：创建时设置 due_at，超时未处理自动标记（`first_response_at`/`due_at` 字段待加）
- 抄送/关注人：扩展工单可见与通知范围（飞书机器人）
- 统计报表：已实现，见 §14（`GET /tickets/stats`）
- 附件支持：工单附件上传

---

## 11. P3 审批挂接（v2，已实现）

工单系统与任务系统（runbook/job_execution）接通，落地任务设计文档 P3 阶段。

### 11.1 数据模型扩展（v14 迁移）

| 变更 | 内容 |
|------|------|
| tickets 新增列 | `runbook_id`（执行意图）、`job_params` JSONB（含 target_resource_ids/params）、`code_ref`（git tag）、`approval_status`（none/pending/approved/rejected） |
| ticket_approvals 表 | 审批记录（不可变）：approver、action（approve/reject）、comment |
| change_freezes 表 | 封禁窗口：name、reason、scope（NULL=全局，数组=模型范围）、起止时间 |
| job_executions.ticket_id | 启用回填（工单自动下发时写入） |

### 11.2 审批策略（风险分级）

- 阈值：`runbook.risk_level ∈ {medium, high, critical}`（`job_service.APPROVAL_RISK_LEVELS`）——v20 修正：建单侧审批改由事项 default_risk 快照驱动，此阈值仅保留为 job 下发侧兜底，见 §13.2.1
- 达标：创建时工单进入 `pending_approval`（approval_status=pending），需 `POST /{id}/approve` 审批；通过→转 open；拒绝→cancelled。创建人不得自批（超管除外）
- 低危：自动直通转 open；**下发一律由运维显式执行**（v20 起无创建/审批自动下发）
- 高危兜底门控：`job:create` 时中高危 runbook 必须携带已审批工单（超管除外），绕道直接下发被拒绝（403）
- 审批门禁态禁止通过 `/{id}/status` 绕过流转（422）
- 下发失败（如封禁期命中、Kafka 不可用）：工单保留，失败原因落入流转记录（[dispatch-failed]）

### 11.3 风控栅栏：封禁窗口（change_freezes）

- CRUD：`GET/POST /api/v1/tickets/freezes`、`DELETE /freezes/{id}`
- 门控点：`job_service.create_execution` 下发前校验，命中全局/模型范围封禁 → 409（工单自动下发路径同样生效）
- scope：NULL=全局；`["aliyun_ecs", "gcp_compute"]` 等仅限定模型范围
- 权限：`change_freeze:list/create/delete`

### 11.4 变更上下文聚合（判断变更时点）

`GET /api/v1/tickets/change-context?resource_ids=1,2,3`（`ticket:list`），每个资源返回：
- 近 7 天变更记录（cmdb_change_logs，每资源 Top 5）
- 占用中的任务执行（同并发目标锁口径）
- 当前命中的封禁窗口（全局/模型范围）
- 环境解析：K8s 读 `k8s:env`、云资源读 `env` 标签（manual 优先、cloud 兜底，与展示同源）；无值返空，门控侧按 fail-safe 处理（`ENV_FAILSAFE_DEFAULT="production"`，从严）

变更后落地判断：job 执行完自动写 `cmdb_change_logs(source='job')`，工单详情聚合 `job_execution` 摘要（状态/起止时间）回显。

### 11.5 新增权限码（已同步 schema.sql / v14 / init_data.py）

| 权限码 | 说明 | 预置角色 |
|--------|------|----------|
| `ticket:approve` | 审批工单 | admin、operator |
| `change_freeze:list` | 查看封禁窗口 | 全角色 |
| `change_freeze:create/delete` | 维护封禁窗口 | admin |

**利害关系人免底（v19）**：纯 RBAC 不识别“被派单人”身份，值班用户无宽泛角色时无法处理自己的工单。现 `require_ticket_permission` 在角色权限不足时对工单**创建人/处理人**免底开放 `ticket:list`/`ticket:update`/`ticket:create`（查看/推进状态/评论）；列表接口无 `ticket:list` 时仅返回自己参与的工单。`approve`/`assign`/`delete`/`dispatch` 仍为角色专属。

**处理权归属矩阵（v19，服务层强校验）**：处理权跟**指派**走，不跟角色走——operator 角色不再等于全能处理权。

| 动作 | 超管 | 处理人 | 创建人 | 其他 |
|------|------|--------|--------|------|
| 开始处理 / 解决 | ✅ | ✅ | ❌ | ❌ |
| 重开（resolved→in_progress） | ✅ | ✅ | ✅ | ❌ |
| 取消 | ✅ | ✅ | ✅ | ❌ |

未指派工单仅超管可处理；超管处理时**自动认领**（assignee=操作者，流转记录 `[self-claim]` 留痕），保证已处理工单必有归属（统计/SLA 可归因）。根治未指派单：目录分类配默认处理组。

---

## 12. 服务目录/处理组/值班派单（v15，已实现）

对齐飞书多维表格 IT 服务工单设计（仅系统运维范围），落地轻量服务目录与值班自动派单。

### 12.1 数据模型（v15 迁移）

| 表 | 说明 |
|----|------|
| `ticket_catalog` | 两级服务目录：一级分类（云账号与权限/资源交付与变更/K8s 平台/发布与配置变更/故障与排查/日常运维支持/主机与 OS）→ 二级事项；事项携带 difficulty/default_risk/default_type/default_group_id |
| `ticket_groups` | 处理组（name + members JSONB），派单/值班最小单位，与 RBAC 角色解耦 |
| `oncall_schedules` | 值班表：日期 × 组 × 三线（tier1 自动派单/tier2 升级建议/tier3 变更审批来源），同组同日期唯一 |
| tickets 扩列 | `catalog_item_id`、`group_id`、`difficulty`（目录快照）、`started_at`（开始处理时间） |

### 12.2 建单语义（目录驱动）

- 选二级事项建单 → 快照 difficulty 与 default_risk（`tickets.risk_level`，驱动审批门控）；未显式传 ticket_type 时取事项 `default_type`；runbook 不预绑——下发时由处理人选择（v20）
- 带处理组且未显式指派 → 按当日值班 tier1 轮转自动派单（复刻多维表格“新增工单自动赋值处理人”自动化），落 `[auto-oncall]` 流转记录
- 处理时长不再手工填：响应时长 = started_at - created_at，处理时长 = resolved_at - started_at（open→in_progress 时自动写 started_at）

### 12.3 API

| 前缀 | 端点 | 权限 |
|------|------|------|
| `/api/v1/ticket-catalog` | GET/POST/PUT/{id}/DELETE/{id}；另提供语义端点 `POST /categories`（一级分类，无事项属性）与 `POST /items`（二级事项，parent_id 必填） | `ticket_catalog:*` |
| `/api/v1/ticket-groups` | GET/POST/PUT/{id}/DELETE/{id} | `ticket_group:*` |
| `/api/v1/oncall-schedules` | GET/POST/PUT/{id}/DELETE/{id} | `oncall:*` |

工单列表新增 `group_id`/`catalog_item_id` 过滤；响应携带目录名/分类名/组名/难度/started_at。

### 12.4 角色分配

admin 全量；operator 管理但无删除；viewer/auditor 只读。目录删除保护：有子项或被工单引用时拒绝。

**值班×处理组约束**：值班三线人选必须 ⊆ 处理组成员池（创建/更新排班时写校验，越界 422 并列出非法 ID）；组成员=可值班人选池，想排外人先加入组。已被移组的在值人员可完成当天值班（不追溯）。

**范围录入、按天存储**：排班表单支持日期范围（`oncall_date`~`end_date`，含端点，上限 31 天），后端按天展开批量建单；范围内已排班的日期**跳过并经 `skipped` 回报**（不静默覆盖），单日模式（不传 end_date）撞已有排班仍 409。存储保持一天一行：当日值班等值查询、UNIQUE(group,date)、中途改人=编辑当天行，均无需区间重叠/拆分逻辑。**契约不强制周对齐**（保留非周周期表达力：按天轮转/双周/节假日特护/周中接手）；周轮转的便捷性由前端快捷方式提供：范围选择器旁置「本周/下周」按钮自动填周一~周日，列表页可按周分组展示（纯前端聚合）。

**请假/替班（中途改人）**：编辑当天行即可（`GET /oncall-schedules?date_from=&date_to=` 同值定位单日 → `PUT /{id}` 改 tier），其余天不受影响；替班人须 ⊆ 组成员池（update 同校验）；建议将替班原因写入当天 `note` 留痕（如“A 休假，B 替”）。改后当日自动派单立即跟随新 tier1 池，无需额外同步。前端推荐周视图日历（7 列 × tier 行，点单元格编辑当天）。

**值班表单联动规范**：tier1/2/3 人员选择器必须以所选处理组为数据源——选定组后改调 `GET /api/v1/ticket-groups/{id}/candidates`（返回该组活跃成员，即唯一合法值班池），**禁止用全量用户列表**；未选组时人员选择器禁用。后端写校验为兜底防线。

### 12.5 后置项

- 附件能力（待存储选型：OSS/本地）
- tier3 作为变更默认审批人的自动挂接（当前审批人仍为手动指定）

---

## 13. 执行目标统一与前端对接规范（v16）

### 13.1 目标字段统一（v16 迁移）

- `tickets.target_resource_ids` JSONB 一等列：**多选唯一入口，语义为“执行目标”，由运维在 `POST /{id}/dispatch` 时填写**（v18 修正：申请类是“造资源”，建单时存量资源尚不存在，提单人不选资源）；建单 API 传值仍兼容（视为运维 API 路径）
- `tickets.business_app_id`（v18）：建单时挂靠**业务应用**作上下文（申请类开通归属哪个 app），替代资源选择器的上下文职能
- `related_resource_id` 标记废弃（保留兼容，存量回填进目标列表）
- 必填规则：建单时目标一律可选（提单人不选资源）；**下发时目标必填**（`dispatch_ticket_job` 校验：本次传入优先，否则用工单已有目标，皆空拒绝）
- 工单列表新增 `target_resource_id` 过滤（JSONB @> 包含语义）
- 变更上下文（`/change-context`）新增 `active_tickets`：影响该资源的活跃工单（pending_approval/open/in_progress），与 busy_execution_id 一起支撑变更时点判断

### 13.2 路由配置化与表单布局（v17）

- `ticket_catalog.default_group_id`：目录关联默认处理组（事项级覆盖分类级）；建单时组自动派生，**提单人不再手选处理组/处理人**（新建表单已去除两字段；API 保留 assignee_id/group_id 作兼容与人工改派）
- **自动分派规则**（组内多人时）：
  1. 当日值班表 tier1 轮转优先
  2. 未配值班 → 回退组成员轮转
  3. 轮转算法 `pool[当日该组工单数 % len(pool)]`，两人组即逐单交替
  4. 均无 → 暂不指派，后续 `POST /{id}/assign` 人工指派/转派
- 建单表单布局规范（语义分组）：
  1. 标题*（整行）
  2. [类型] [优先级] 同行
  3. [服务目录事项] → 处理组/处理人自动带出（详情只读展示，不在表单出现）
  4. 执行目标（多选，整行）
  5. 描述（整行）
- 目录配置表单：分类表单含“默认处理组”；事项表单含“覆盖默认处理组”（可选）

### 13.2.1 角色分离与执行下发（v18 修正）

**问题**：runbook/git tag/JSON 参数是执行层概念，提单人（开发/测试）不知道也不该接触。

**修正后的角色分层**：

| 角色 | 动作 | 接口 |
|------|------|------|
| 提单人 | 选事项+关联应用+描述，**不接触 runbook/目标** | `POST /tickets` |
| 审批人 | 中高危审批 | `POST /{id}/approve` |
| 执行人（运维） | 选 runbook + 补 git tag + 参数 + 执行目标 → 下发 | `POST /{id}/dispatch`（`job:create`） |

- runbook 是执行工具而非事项属性（同一事项可对应多个 runbook，如「Agent/组件部署」→ node_exporter / 安全 agent 各用不同 playbook），**由处理人下发时选择**（v20；`ticket_catalog.default_runbook_id` 已移除）
- 审批门控由事项 `default_risk` 快照（`tickets.risk_level`）驱动，与执行工具解耦；job 侧兜底不变：中高危 runbook 下发必须携带已审批工单
- `POST /{id}/dispatch` 校验：工单 open、runbook 存在且启用、params 过 params_schema、目标非空、无活跃执行（防重复下发）
- 新建表单最终字段：标题* / 类型 / 优先级 / 服务目录事项 / 关联应用（可选）/ 描述（**无 Runbook、无 git tag、无 JSON、无资源选择器**）
- 工单详情：open 状态对运维显示“选 runbook 并下发”按钮

### 13.3 前端对接规范

1. **表单永远选人/选物，不填 ID**：所有关联实体字段用可搜索选择器（remote-select），提交时仅发 id
2. 关联资源选择器数据源：`GET /api/v1/cmdb/resources/options?keyword=`（轻量字段：id/name/model_code/provider/region/status；名称+实例 ID 双模糊匹配）
3. 资源多选选择器（`target_resource_ids`）**只出现在运维下发弹窗**，不出现在新建表单；新建表单的上下文挂靠用「关联应用」选择器（可选，数据源 `GET /api/v1/cmdb/apps?keyword=`）；下拉项渲染 `name（model_code / region）`
4. 目录事项为下拉（数据源：/ticket-catalog，仅列二级事项）；处理组/处理人不在新建表单出现（自动派生/自动分派）
5. **处理组→处理人联动**（仅改派场景）：`POST /{id}/assign` 的人工改派弹窗用 `GET /ticket-groups/{id}/candidates`（组成员 ∪ 当日值班三线）列候选人；新建表单不出现处理人/处理组
6. 新建工单弹窗字段：标题* / [类型][优先级] / 服务目录事项 / 关联应用（可选）/ 描述；**无资源选择器、无处理组/处理人**（组自动派生、人自动分派）
7. 工单详情资源回显：按 `target_resource_ids` 批量调 `/cmdb/resources/{id}` 取 name 展示
8. **下发弹窗动态表单（禁 JSON 文本框）**：先选 runbook（数据源 `GET /api/v1/runbooks` 列表），再按其 `params_schema` 逐条渲染控件——string→输入框（有 `enum`→下拉）、number→数字框、boolean→开关；`default` 作初值、`required` 标必填、`description` 作字段标题/提示；**执行目标资源多选为本弹窗必填项**（数据源 `/cmdb/resources/options`）；提交组装为 `runbook_id` + `params` + `target_resource_ids` 调 `POST /{id}/dispatch`。后端校验时自动回填 default，表单只收集用户实际填写项

---

## 14. 统计报表（仪表盘）

`GET /api/v1/tickets/stats?date_from=&date_to=&group_id=`（权限 `ticket:list`）

响应结构：

```json
{
  "totals": {"total": 120, "open": 5, "pending_approval": 2, "in_progress": 3, "resolved": 40, "closed": 65, "cancelled": 5},
  "time": {"avg_response_minutes": 35.2, "avg_handle_minutes": 240.5},
  "by_assignee": [{"user_id": 1, "name": "绿豆饼", "assigned": 60, "done": 55,
                   "avg_response_minutes": 30.0, "avg_handle_minutes": 200.0}],
  "by_category": [{"category": "资源交付与变更", "total": 45}],
  "trend": [{"date": "2026-08-01", "created": 6, "resolved": 4}]
}
```

| 块 | 用途 | 图表建议 |
|----|------|----------|
| totals | 状态分布/存量 | 卡片 + 环图 |
| time | 平均响应/处理时长（SLA 大盘） | 卡片 |
| by_assignee | 处理数/完成数/人均时效（按完成数降序） | 柱状图/表格 |
| by_category | 分类占比（二级事项归一级分类，无目录=未分类） | 环图 |
| trend | 每日创建 vs 解决 | 双折线 |

时间口径：响应=started_at-created_at；处理=resolved_at-created_at 中的解决段（resolved_at-started_at）；SQL 层 `extract(epoch ...)` 计算，avg 自动忽略 NULL。

---

## 15. 飞书机器人私聊通知（已实现）

工单关键节点通过飞书应用机器人私聊卡片触达相关人员。发送层收敛在 `core/feishu_bot.py`（通用客户端），业务挂点在 `ticket_service` 状态流转函数 `session.commit()` 之后；与告警模块“执行器直发”链路互不依赖。

### 15.1 通知场景

| 触发点 | 收件人 | 卡片 | 说明 |
|--------|--------|------|------|
| 创建工单（显式指派或值班自动派单） | 处理人 | 「工单已指派给你」（黄头） | 自派给自己跳过（无信息量） |
| 指派/转派（处理人变化才通知） | 新处理人 | 同上 | 处理人未变化不发 |
| 状态推进 → resolved（首次） | **建单人** | 「工单已解决」+ 处理说明 | 重开后再解决会再次通知 |

卡片字段：编号/优先级/标题/建单人/指派操作人/处理说明；`BINGOPS_TICKET_NOTIFY_WEB_BASE_URL` 非空时附「查看工单」跳转按钮（`{base}/{ticket_id}`）。

### 15.2 发送层与可靠性

- 复用飞书 SSO 同一自建应用（app_id/app_secret）→ `tenant_access_token`（进程内缓存，飞书侧 2h 有效期提前 60s 刷新）→ IM API `send_interactive(open_id, card)`（msg_type=interactive，content 为卡片 JSON 二次序列化）
- **fire-and-forget**：`asyncio.create_task` 外发，失败只记日志不阻断工单主流程；任务集合带 done callback 自回收
- **可达性**：收件人 `users.feishu_open_id` 为空（纯账密未走 SSO）→ 跳过并留日志；重复通知由状态机门禁保证（resolved 仅首次流转触发）

### 15.3 配置与前置条件

| 环境变量 | 默认 | 说明 |
|----------|------|------|
| `BINGOPS_TICKET_NOTIFY_ENABLED` | `false` | 总闸（fail-safe，对齐告警联动先例）；上线须显式置 true |
| `BINGOPS_TICKET_NOTIFY_WEB_BASE_URL` | 空 | 前端工单详情页基址；非空才带「查看工单」按钮 |

两者启动时读取一次，修改后需重启服务。**管控边界**：总闸仅管控事件类通知（解决/指派提醒）；飞书建单交互的即时反馈（表单提交后的 toast 与「工单已创建」确认卡片，见 §16.2）不经过此开关，始终发送——否则用户点提交会无任何响应。飞书侧前置：应用开通「机器人」能力 + `im:message:send_as_bot` 权限并发布版本；接收人在应用可用范围内。新增通知场景只需在对应挂点构卡后调用 `_notify_feishu_user`，无需改模型或 API。总闸关闭时状态流转钩子静默跳过（debug 日志 `Ticket notify gate disabled` 留痕），排障时若 BINGOPS_LOG_LEVEL=INFO 看不到，可临时调 DEBUG 或直接查容器环境变量 `env | grep TICKET_NOTIFY`。

---

## 16. 飞书机器人建单（入站链路，已实现）

飞书侧反向建单：用户在机器人对话里点菜单/发指令，经卡片表单选择目录事项后直接建单。与网页端 `POST /tickets` 完全同构（同一 `create_ticket`，目录必填、default_type/default_risk 快照、审批门禁全部生效）。

### 16.1 回调端点与安全模型

统一回调入口 `POST /api/v1/integrations/feishu/events`（`feishu_events.py`，`response_model=None`），同时承载三类报文：

| 报文 | 处理 |
|------|------|
| `url_verification` 握手 | 保存请求地址时飞书即时 POST challenge，1s 内原样回显 |
| `im.message.receive_v1`（事件推送） | 单聊文本识别建单指令 → 回表单卡片；发卡片一律 fire-and-forget，端点秒级 ack |
| `card.action.trigger`（卡片交互回调） | 表单提交同步处理（本地 DB 耗时可控），**响应体即 toast** |

安全模型（fail-closed，对齐告警 webhook 先例）：Encrypt Key / Verification Token 未配置 → 全部 401；报文带 `encrypt` 字段按 AES-256-CBC 解密（key=SHA256(Encrypt Key)，依赖 `cryptography`，零新增依赖）；token 不匹配 401。

**幂等**：`event_id` 进程内 LRU 去重（容量 2000）——飞书对超时回调会重推，重复回调直接跳过，防止重复建单。注意去重为进程级，多副本极端场景（重推路由到另一副本）存在理论窗口，量级可忽略。

### 16.2 建单交互流（`feishu_event_service.py`）

1. 用户点机器人菜单「创建工单」（预设文本「建单」）或直接发文本 → 关键词匹配（`建单/创建工单/建工单`，前缀或全等）
2. `open_id → users.feishu_open_id` 查平台账号；未绑定 → 回引导卡片（提示先完成一次飞书登录，登录即自动绑定）
3. 回「新建工单」表单卡片（**卡片 JSON 2.0**，`schema: "2.0"` + form 容器）：
   - **服务目录事项**：全量活跃二级事项实时读库（`ticket_catalog`，显示「一级分类/二级事项」），`required: true` ——非固定兜底，选哪个按哪个建
   - **优先级**：low/medium/high/urgent 下拉
   - **标题**（必填，服务端截断 256）/ **描述**（选填）
   - 提交按钮 `form_action_type: "submit"`
4. 提交 → `card.action.trigger` 携带 `action.form_value` → `has_permissions(user, "ticket:create")` 校验 → `create_ticket(operator=该飞书用户)` → toast「工单 TK-xxx 已创建」+ 绿色确认卡片（复用 `BINGOPS_TICKET_NOTIFY_WEB_BASE_URL` 跳转按钮）

**工单类型不出现在卡片上是刻意的**：未显式传 ticket_type 时由所选事项 `default_type` 快照派生（§12.2），default_risk/处理组路由同样随目录带出；目录必填约束保证类型永不为空，与网页端建单同构。卡片 JSON 2.0 协议要点（实测踩坑）：`select_static`/`input` 无 `label` 属性（字段名用独立 markdown 行），`options` 元素用 `text` 对象而非 `label`，提交按钮用 `form_action_type` 而非历史属性 `action_type`。

### 16.3 配置与飞书侧前置

| 环境变量 | 默认 | 说明 |
|----------|------|------|
| `BINGOPS_FEISHU_EVENT_ENCRYPT_KEY` | 空 | 回调报文解密（事件与回调 → 加密策略）；未配置拒绝全部回调 |
| `BINGOPS_FEISHU_EVENT_VERIFICATION_TOKEN` | 空 | 回调验签；未配置拒绝全部回调 |

飞书侧订阅矩阵（最小集）：事件 `im.message.receive_v1` + 权限 `im:message.p2p:readonly`；回调配置订阅 `card.action.trigger`（请求地址与事件同 URL）；机器人菜单（发送文字消息「建单」）为可选入口装饰。群 @ 建单预留 `im.message.group_at_msg:readonly`（二期，`chat_type != p2p` 当前静默忽略）。明确不订阅：消息已读、消息资源文件、机器人进退群、飞书审批应用事件（平台工单走自身数据表）。

---

## 17. 工单附件（v38，已实现）

附件对象实体存 **OSS 私有桶**，数据库只存元数据；下载一律经**平台代理端点**鉴权后流出，**不下发 OSS 签名 URL**（签名会过期且绕过 RBAC）。

### 17.1 存储与约束

| 项 | 约定 |
|----|------|
| 元数据表 | `ticket_attachments`（ticket_id / filename / size / content_type / oss_key / uploaded_by / created_at），工单删除级联 |
| 对象键 | `{BINGOPS_OSS_ATTACHMENT_PREFIX}/ticket/{ticket_id}/{uuid}/{filename}`（中间 `ticket/` 段为对象类别命名空间，便于 RAM 按类别授权） |
| 体积 | 单文件 ≤ `BINGOPS_TICKET_ATTACHMENT_MAX_MB`（默认 20MB）；单工单 ≤ 50 个 |
| 类型白名单 | 图片 `png/jpg/jpeg/gif/webp/bmp`、文本 `txt/log/json/csv/md/yaml/yml/sql`、文档包 `pdf/zip/gz/tgz/tar` |
| 状态闸 | 仅 `open` / `pending_approval` / `in_progress` 可上传/删除；**终态（resolved/closed/cancelled）只读**防篡改 |
| 删除权 | 仅**上传者本人**或超管 |

### 17.2 API 契约

| 端点 | 权限 | 说明 |
|------|------|------|
| `GET /api/v1/tickets/{ticket_id}/attachments` | `ticket:list`（利害关系人免底） | 附件列表 |
| `POST /api/v1/tickets/{ticket_id}/attachments` | `ticket:attachment`（利害关系人免底） | 上传，`multipart/form-data`，字段名 **`file`** |
| `GET /api/v1/tickets/{ticket_id}/attachments/{attachment_id}/download` | `ticket:list`（利害关系人免底） | 下载（**二进制流，不走统一信封**） |
| `DELETE /api/v1/tickets/{ticket_id}/attachments/{attachment_id}` | `ticket:attachment`（利害关系人免底） | 删除（上传者/超管 + 非终态） |

附件对象结构（工单详情 `attachments` 数组同此形状）：

```json
{
  "id": 12,
  "ticket_id": 7,
  "filename": "变更前截图.png",
  "size": 184320,
  "content_type": "image/png",
  "uploaded_by": 3,
  "uploader_name": "绿豆饼",
  "download_url": "/api/v1/tickets/7/attachments/12/download",
  "created_at": "2026-10-23T09:12:31Z"
}
```

`GET /api/v1/tickets/{ticket_id}` 的响应已内嵌 `attachments` 数组，详情页无需再单独请求列表。

### 17.3 前端对接说明（重要）

1. **上传必须在建单之后**：附件表依赖 `ticket_id`。建单流程 = `POST /tickets` 拿到 `id` → 再调上传端点。建单弹窗建议提交成功后切到“补充附件”步骤（或跳详情页上传）。
2. **下载必须用 `fetch` + blob，不能用 `<a href>` 直链**：下载端点需要 `Authorization` 头，浏览器直接跳转带不上 → 会 401。正确写法：

```ts
const res = await fetch(att.download_url, { headers: { Authorization: `Bearer ${token}` } });
const blob = await res.blob();
// 用 URL.createObjectURL(blob) + 临时 <a download> 触发保存
```

3. **上传示例**：

```ts
const fd = new FormData();
fd.append("file", file);            // 字段名固定为 file
await fetch(`/api/v1/tickets/${ticketId}/attachments`, {
  method: "POST",
  headers: { Authorization: `Bearer ${token}` },   // 不要手动设 Content-Type
  body: fd,
});
```

4. **按钮可见性**：上传/删除按钮仅在 `status ∈ {open, pending_approval, in_progress}` 且当前用户为**创建人/处理人**或拥有 `ticket:attachment` 权限时显示；终态工单隐藏全部写操作，只留下载。
5. **前端预校验**：提交前检查扩展名白名单与体积（≤20MB），避免无谓请求；后端仍会二次校验（422 与错误信息直出）。
6. **OSS 未启用时端点返 503**：前端应展示“附件存储未启用，请联系管理员”而非报网络错误；功能开关见 17.4。
7. 附件支持**前端预览**（无需后端改动）：用现有下载端点 `fetch` 成 blob 后，图片置 `URL.createObjectURL(blob)` 到 `<img>`、PDF 置 `<iframe src>`、文本用 `blob.text()` 渲染。注意 `Content-Disposition: attachment` 只影响浏览器**直接导航**，对 fetch+blob 路径不生效，所以不影响预览。若要“新标签直接打开”或 `img src` 秒开，需后续加预签名 URL 端点（要求 OSS 公网可达）。

### 17.4 配置与部署（运维）

| 环境变量 | 默认 | 说明 |
|----------|------|------|
| `BINGOPS_TICKET_ATTACHMENT_ENABLED` | `false` | 总闸；关闭时附件端点直接 503（fail-closed） |
| `BINGOPS_OSS_ENDPOINT` | 空 | 如 `oss-cn-hangzhou.aliyuncs.com`（不带 scheme） |
| `BINGOPS_OSS_BUCKET` | 空 | 私有桶名 |
| `BINGOPS_OSS_ACCESS_KEY_ID` / `BINGOPS_OSS_ACCESS_KEY_SECRET` | 空 | **平台自身出站凭据**（RAM 子账号，仅该桶 PutObject/GetObject/DeleteObject）；与业务密钥分层：业务密钥走 Vault 引用由 runner 解析，平台自身集成凭据走环境变量（同飞书 app_secret） |
| `BINGOPS_OSS_ATTACHMENT_PREFIX` | `ticket-attachments` | 对象键前缀 |
| `BINGOPS_TICKET_ATTACHMENT_MAX_MB` | `20` | 单文件上限 |

部署步骤：

1. 执行迁移（建表 + 权限种子 + admin/operator 授权）：
   ```bash
   psql -v ON_ERROR_STOP=1 --single-transaction -U <user> -d <dbname> \
     -f sql/migrations/v38_ticket_attachments.sql
   ```
2. 配置上述环境变量并重启服务；桶保持**私有**读写（不要开公共读）。
3. 已有环境的其他角色需要 `ticket:attachment` 时，在角色管理界面手工勾选（init_data 的角色授权是总量幂等，重跑不会补入新码）。

---

## 18. SQL 变更工单（P1-a/P1-b 已实现；P1-c 待做）

> **已落地（P1-a，v39）**：`change_sql` / `sql_hash` / `rollback_sql` / `rollback_kind` / `ddl_strategy` / `snapshot_ref` 字段；`ticket_catalog.requires_sql` / `sql_scope`；sqlglot 校验器（`services/sql_change_service.py`）与建单集成（不可逆变更强制回滚方案）。
> **已落地（P1-b）**：下发弹窗重贴 SQL 并与 `sql_hash` 比对（不一致 422，需重新审批）；`ddl_strategy` 枚举校验与引擎适配（PolarDB 拒绝 ptosc/ghost）。
> **待做**：P1-c（runner 侧环境预检 / binlog 位点采集与反解）。

### 18.1 定位与三原则

SQL 变更是**工单类型插件**而非独立模块（不拆：审批/状态机/封禁/审计/通知/权限/统计七套能力工单已有）。

| 原则 | 含义 |
|------|------|
| **审批所见即所执行** | `sql_hash` 锁定：审批的 SQL 与执行的 SQL 字符串不一致 → 422，必须重新审批 |
| **回滚分层** | 可逆语句自动生成（auto）/ 不可逆强制人工方案（manual）/ 执行前快照兜底（snapshot） |
| **执行方式可声明** | `ddl_strategy` 区分直执行 / pt-osc / gh-ost / 人工，不同策略对应不同回滚路径 |

### 18.2 数据模型（v39 已落地）

| 表 | 新增列 | 说明 |
|----|--------|------|
| `tickets` | `change_sql` TEXT | 变更 SQL（审批对象） |
| | `sql_hash` VARCHAR(64) | sha256(规范化 SQL)，审批↔执行一致性锚点 |
| | `rollback_sql` TEXT | 回滚 SQL（自动生成或人工填写） |
| | `rollback_kind` VARCHAR(16) | `auto` / `manual` / `none` |
| | `ddl_strategy` VARCHAR(16) | `direct` / `ptosc` / `ghost` / `manual`（默认 direct） |
| | `snapshot_ref` VARCHAR(512) | 执行前快照引用（OSS key 或 job_step 标识） |
| `ticket_catalog` | `requires_sql` BOOLEAN | 该事项是否必须填 SQL（DDL/DML 类事项） |
| | `sql_scope` VARCHAR(8)（可选） | `dml` / `ddl` / `both`（默认 both）；「数据订正」设 `dml` 拦截 DDL |

**目录侧怎么配（不新建分类体系）**：SQL 工单不是新模块、也不是新菜单，而是在现有服务目录事项上打标记：

| 事项 | 分类 | `requires_sql` | `sql_scope` |
|------|------|---------------|------------|
| 数据库DDL/DML变更 | 发布与配置变更 | ✅ | both |
| 数据订正 | 故障与排查 | ✅ | dml |
| 其他（计算资源申请/应用发布等） | — | ❌ | — |

判定标准：**执行载体是否为一条 SQL**。是 → 打标记（表单出现 SQL 区、执行走 sql-exec）；否 → 不打（仍走下发时选 runbook 的通用路径）。拆分粒度按“审批人 / 风险等级 / 语句范围是否不同”决定，**禁止按具体 SQL 内容建事项**（事项是服务类型，不是变更内容）。

### 18.3 流程

```
① 提单（开发/测试）  选 requires_sql 事项 → 表单出现 SQL 编辑区
                    → 服务端校验（18.4）→ 落 change_sql + sql_hash
                    → risk=high 进 pending_approval
② 审批（DBA/架构师） SQL 全文 + 校验结果 + 回滚方案（auto 预览 / manual 必填）
③ 下发（运维）       选 runbook(sql-exec) + 目标 DB 资源 + db_user + db_password 凭据
                    + 重贴 SQL（hash 比对）→ 创建 job
④ 执行（runner）     step0 环境探测 → step1 记录 binlog 位点 → step2 执行 → step3 反解回滚
⑤ 归档               rollback_sql 回填工单；大脚本/快照走 §17 附件通道
⑥ 回滚（人工）       工单展示 rollback_sql → 运维确认后作为新 execution 执行（不自动回滚）
```

### 18.4 静态校验器

- 依赖：**引入 `sqlglot`**（纯 Python 解析库，支持 MySQL/PG 方言）；**不引入 goInception**（MySQL-only 常驻服务，且其回滚依赖自身执行通道，与 runner 执行链冲突）。
- 用途：语句类型判定、表名提取、有无 WHERE 判定、DDL 逆向预审。
- 规则：

| 规则 | 动作 |
|------|------|
| `DROP DATABASE/SCHEMA`、`TRUNCATE` | 拒绝（422） |
| `UPDATE`/`DELETE` 无 `WHERE` | 拒绝 |
| 语句数超限（默认 1 条） | 拒绝 |
| `DROP COLUMN` / 改类型 / `DROP TABLE` | 放行但 `rollback_kind=manual`，强制人工回滚方案 + 执行前快照 |
| `CREATE TABLE` / `CREATE INDEX` / `ADD COLUMN` / `RENAME` | `rollback_kind=auto`（AST 逆向） |
| `INSERT` / 带 WHERE 的 `UPDATE`/`DELETE` | `rollback_kind=auto`（binlog 反解） |

> sqlglot 只做解析与预审；**DML 的回滚值以 binlog before image 为唯一事实源**，绝不以静态解析推断。

### 18.5 回滚能力矩阵

**DML（MySQL 系列，binlog 反解）**

| 正向 | 反向生成 | 依据 |
|------|---------|------|
| `INSERT` | `DELETE WHERE pk IN (...)` | after image 主键 |
| `UPDATE` | `UPDATE ... SET 旧值 WHERE pk` | **before image**（含触发器/级联连带修改） |
| `DELETE` | `INSERT ... VALUES (完整旧行)` | before image 全列 |

**DDL（按可逆性三档）**

| DDL | 回滚 | 自动 |
|-----|------|------|
| `CREATE TABLE` | `DROP TABLE` | ✅ |
| `CREATE INDEX` | `DROP INDEX` | ✅ |
| `ADD COLUMN` | `DROP COLUMN` | ⚠️ 结构可逆，但变更后已写入该列的数据会丢，需人工确认 |
| `RENAME TABLE` | 反向 RENAME | ✅ |
| `DROP COLUMN` | `ADD COLUMN` 只能恢复结构 | ❌ 数据必须靠执行前快照 |
| `MODIFY COLUMN`（改类型/长度） | — | ❌ 截断/溢出不可逆 |
| `DROP TABLE` / `TRUNCATE` | — | ❌ 只能备份恢复 |

### 18.6 执行方式（`ddl_strategy`）与引擎适配

| 策略 | 适用 | 回滚方式 |
|------|------|---------|
| `direct` | 小表直接 DML/DDL | binlog 反解 / AST 逆向 |
| `ptosc` | RDS MySQL / 自建 MySQL 大表 DDL | **保留旧表 + RENAME 回切**（非 binlog 反解） |
| `ghost` | 同上（不依赖触发器） | 同上（从 cut-over 日志取表名映射） |
| `manual` | PolarDB 内核 DDL / DBA 自行执行 | 人工回滚方案 |

**大表判定（runner step0）**：`SELECT table_rows FROM information_schema.tables` + 数据体积，超阈值（建议 100 万行 / 10GB）→ 拒绝 `direct`，要求改选在线 DDL 策略。

### 18.6.1 环境预检（版本与 binlog 配置）

预检分两级，共用同一份**只读**脚本（`sql-precheck`）：

| 级别 | 时机 | 作用 |
|------|------|------|
| **前置预检**（推荐） | dispatch 之前单独跑一条只读 job | 提前暴露“该库无法自动回滚”，避免执行完才发现；结果回填工单决定 `rollback_kind` |
| **执行复检** | sql-exec 的 step0 | 防配置漂移（执行前一刻再确认一次） |

**检查项（MySQL 8.0 专项）**：

| 检查 | 通过条件 | 不满足时 |
|------|---------|---------|
| `SELECT VERSION()` | 命中已验证支持矩阵（当前以 8.0 为主） | 未验证版本 → 告警但可降级快照 |
| `binlog_format` | 必须 `ROW` | 硬失败（无法反解） |
| `binlog_row_image` | 必须 `FULL` | 硬失败（DELETE→INSERT 会缺列） |
| `binlog_row_metadata`（8.0 新增，默认 MINIMAL） | `FULL` 最佳；`MINIMAL` → 用 information_schema 补全列定义 | 降级：靠元数据补全 |
| `binlog_transaction_compression`（8.0.20+，默认 OFF） | 建议 `OFF` | 开启时需解析库支持压缩事件，否则拒绝自动回滚 |
| `binlog_expire_logs_seconds`（8.0 变量名，勿用 5.7 的 `expire_logs_days`） | ≥ 回滚窗口（建议 7 天） | 回滚窗口内 binlog 可能已被清理 → 快照兜底 |
| 复制权限 | `REPLICATION SLAVE` + `REPLICATION CLIENT` | 无权限 → 快照路线 |
| 连接驱动依赖 | 认证插件为 `caching_sha2_password` 时，runner 需 `cryptography` 包 | 缺依赖 → 连接直接失败，镜像需预装 |

预检结果以 JSON 落 `job_steps` 输出（版本/各项配置/大表判定/是否可自动回滚），供前端展示与工单归档。

### 18.7 pt-osc / gh-ost 的引用方式

**平台不内置、不捆绑这两个工具**，只做三件事：

1. **策略声明**：工单 `ddl_strategy=ptosc|ghost`，runbook `sql-exec` 的 params 携带工具参数（表名、chunk-size、max-lag 等）。
2. **参数强制校验**（服务层 + runner 双重）：
   - pt-osc 必须带 `--no-drop-old-table`（否则旧表被删，回滚彻底失去依据）
   - gh-ost 不得启用 `--ok-to-drop-table`，且指定 `--cut-over` 后退保留旧表名映射
   - 两者都必须声明 `--alter` 与目标表，且与 `change_sql` 语义一致
3. **回滚生成**：从工具输出中解析旧表名（pt-osc: `_<table>_old`；gh-ost: `_<table>_ghc`/`_del`）→ 生成 `RENAME TABLE <old> TO <table>, <table> TO <tmp>` 回切脚本，写入 `rollback_sql`（`rollback_kind=auto`）。

> 注意：工具执行期间会产生大量影子表 binlog，**该策略下禁止走 binlog 反解**（否则生成的回滚 SQL 全是影子表噪声）。

### 18.8 PolarDB 的策略差异

| 维度 | PolarDB MySQL 版处理 |
|------|---------------------|
| **大表 DDL** | **不需要 pt-osc/gh-ost**——阿里云官方明确：PolarDB 支持**并行 DDL**与**表末尾秒级加列**，内核方式性能远优于第三方拷表工具。`ddl_strategy` 应限定 `direct`（内核 DDL）/`manual`，**拒绝 ptosc/ghost** |
| **binlog 反解** | DMS 对 PolarDB MySQL 提供“精确备份”（binlog 反解），说明可行性成立；但**自研 runner 脚本需实测兼容性**（PolarDB binlog 由内核生成，事件细节与开源 MySQL 存在差异），列待确认项 |
| **兜底** | 拿不准时退“通用备份”（执行前快照受影响行 → 生成恢复脚本），与 PG 同构 |
| **判定依据** | CMDB 目标资源的引擎/型号字段（`model_code`/`engine`）。**待确认：CMDB 是否已录入 PolarDB 模型**（当前 schema.sql 未见 polar 相关种子） |

### 18.9 PostgreSQL（P2，参考 DMS 通用备份）

| 正向 | 自动生成的恢复脚本 |
|------|-------------------|
| `UPDATE` | 执行前快照 → `UPDATE t a SET ... FROM bak b WHERE a.pk=b.pk` |
| `DELETE` | `INSERT INTO t SELECT * FROM bak ON CONFLICT DO NOTHING` |
| `INSERT` | 执行时 `RETURNING pk` → `DELETE WHERE pk IN (...)` |
| DDL | 不可自动，强制人工 + `pg_dump --schema-only` 结构快照 |

### 18.10 风险清单

| 风险 | 应对 |
|------|------|
| **热表回滚覆盖** | 回滚会抹掉窗口内的合法后续写入 → 回滚前提示差异，建议停写/维护窗口，或按 `updated_at > 变更时间` 排除 |
| binlog 保留期不足 | 执行前 step0 校验保留时长；超出 → 只能快照兜底 |
| 无 REPLICATION 权限 | 退快照路线（与 PG 同构），不影响工单流程 |
| MySQL 8.0 事件兼容 | 引入 binlog 解析前先做版本矩阵验证用例 |
| 大事务回滚 SQL 膨胀 | 上限（如 5MB）超出则不回填工单字段，只存 §17 附件供下载 |
| 大表直接 DDL 锁表 | step0 大表判定 → 拒绝 `direct`，要求在线 DDL 策略 |

### 18.11 待确认项

1. MySQL 实例能否提供 `REPLICATION SLAVE/CLIENT` 账号（决定 binlog 路线是否可行）
2. runner 镜像能否安装 `binlog2sql`/`pymysqlreplication`
3. runner 到目标 DB 的网络路径（直连 / 经跳板）
4. CMDB 是否已录入 PolarDB 模型（决定引擎判定依据）
5. PolarDB binlog 与开源解析工具的兼容性实测

### 18.12 实施拆分

| 阶段 | 内容 | 仓库 |
|------|------|------|
| P1-a | v39 迁移 + 字段/校验器（sqlglot）+ 提单与审批展示（后端✅） | bingops |
| P1-b | DispatchRequest 携带 SQL + hash 比对拦截（✅） | bingops |
| P1-c | runner step0 环境探测 / step1 位点 / step2 执行 / step3 反解回传 | bingops-runner |

### 18.13 前端对接说明

#### 字段映射

| 字段 | 出现位置 | 控件 | 约束 |
|------|---------|------|------|
| `change_sql` | 新建工单（仅 `requires_sql=true` 的事项） | 代码编辑器（Monaco，MySQL 高亮 + 行号） | 必填；服务端做 18.4 校验，前端只做非空 |
| `rollback_sql` | 新建工单（仅当服务端返回 `rollback_kind=manual`）、审批页只读 | 代码编辑器 / 只读展示 | `manual` 时必填；`auto` 时由系统生成 |
| `ddl_strategy` | 下发弹窗 | 下拉：direct / ptosc / ghost / manual | 引擎为 PolarDB 时只能选 direct/manual；预检判定大表时禁选 direct |
| `sql_hash` | 前端不展示 | — | 下发时重贴 SQL，服务端比对，不一致 → 422 |
| 预检结果 | 工单详情、下发弹窗提示区 | 键值表 + 警告条 | 硬失败项（binlog 非 ROW / row_image 非 FULL）→ 禁止下发 |

#### 四个页面的交互要求

1. **新建工单**：选事项后根据 `requires_sql` 动态显示 SQL 区；提交失败（422）时把服务端 `message` 里的违规规则原样展示（如“无 WHERE 的 UPDATE 已拦截”）；`rollback_kind=manual` 时展开“回滚方案”必填区。
2. **审批页**：SQL 只读全文（行号 + 语法高亮 + 一键复制）、校验结论（语句类型/影响表/是否无 WHERE）、回滚方案（auto 展示生成的 SQL，manual 展示人工填写的）——**审批人必须能看到完整 SQL 与回滚方案才能点通过**。
3. **下发弹窗（运维）**：runbook(sql-exec) + 目标 DB 资源选择器（限 DB 类模型）+ `db_user` 输入 + `db_password` 凭据下拉（credentials 中 `kind=db_password`）+ **重贴 SQL（`change_sql`，服务端 hash 比对）** + `ddl_strategy`；选 ptosc/ghost 后展开工具参数（表名/chunk-size/max-lag）；预检硬失败时禁用提交并提示原因。
4. **工单详情**：SQL 面板双栏（变更 SQL / 回滚 SQL）+ 预检结果卡 + 执行记录（affected_rows / binlog 位点 / 回滚生成状态）；「执行回滚」按钮仅 `status=open` 且在 `rollback_sql` 非空时显示，点击弹二次确认并提示“回滚将覆盖变更后的写入，请确认无业务写入”。

#### 列表与状态

- 工单列表加「SQL 变更」Tab（筛选 `requires_sql` 事项），列建议：单号 / 标题 / 目标库 / 风险等级 / 审批状态 / 回滚可用性（auto / manual / 不可用）。
- 执行中的回滚生成属**异步**（job step），前端不能同步等：提交后轮询工单详情（或监听飞书通知），回滚 SQL 就绪后再刷新面板。

#### 错误码处理

| 响应 | 场景 | 前端动作 |
|------|------|---------|
| 422 | 高危语法 / hash 不一致 / 预检硬失败 | 原样展示 `message`（已含具体原因与规则名） |
| 409 | 封禁窗口命中 / 目标并发锁 | 展示冲突原因与持有者 |
| 502 | OSS 或外部服务失败（已带 OSS 错误码与 request_id） | 展示“存储/外部服务异常”并附 message |
| 503 | 附件存储未启用 | 提示联系管理员开启开关 |
