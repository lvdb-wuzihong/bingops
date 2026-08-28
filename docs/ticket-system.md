# 工单系统（Tickets）设计文档

> 状态：v3 已实现（v1 协作流转 + P3 审批挂接 + v15 服务目录/值班派单） | 维护者：BingOps Team
> 相关代码：`bingops/models/ticket.py`、`bingops/services/ticket_service.py`、`bingops/api/v1/tickets.py`、`bingops/services/change_freeze_service.py`
> 数据库脚本：`sql/migrations/v4_tickets.sql` + `sql/migrations/v14_ticket_approval.sql`（已同步 `sql/schema.sql`）

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

- 阈值：`runbook.risk_level ∈ {medium, high, critical}`（`job_service.APPROVAL_RISK_LEVELS`）
- 达标：创建时工单进入 `pending_approval`（approval_status=pending），需 `POST /{id}/approve` 审批；通过→转 open 并自动下发 job；拒绝→cancelled。创建人不得自批（超管除外）
- 低危：自动直通，创建即下发（填单即执行）
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

---

## 12. 服务目录/处理组/值班派单（v15，已实现）

对齐飞书多维表格 IT 服务工单设计（仅系统运维范围），落地轻量服务目录与值班自动派单。

### 12.1 数据模型（v15 迁移）

| 表 | 说明 |
|----|------|
| `ticket_catalog` | 两级服务目录：一级分类（云账号与权限/资源交付与变更/K8s 平台/发布与配置变更/故障与排查/日常运维支持）→ 二级事项；事项携带 difficulty/default_risk/default_type/default_runbook_id |
| `ticket_groups` | 处理组（name + members JSONB），派单/值班最小单位，与 RBAC 角色解耦 |
| `oncall_schedules` | 值班表：日期 × 组 × 三线（tier1 自动派单/tier2 升级建议/tier3 变更审批来源），同组同日期唯一 |
| tickets 扩列 | `catalog_item_id`、`group_id`、`difficulty`（目录快照）、`started_at`（开始处理时间） |

### 12.2 建单语义（目录驱动）

- 选二级事项建单 → 快照 difficulty；未显式传 ticket_type 时取事项 `default_type`；未传 runbook 时取事项 `default_runbook_id`（预绑执行意图，接入 P3 审批/直通链路）
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

### 12.5 后置项

- 附件能力（待存储选型：OSS/本地）
- tier3 作为变更默认审批人的自动挂接（当前审批人仍为手动指定）

---

## 13. 执行目标统一与前端对接规范（v16）

### 13.1 目标字段统一（v16 迁移）

- `tickets.target_resource_ids` JSONB 提升为一等列：**多选唯一入口**
- `related_resource_id` 标记废弃（保留兼容，存量回填进目标列表）
- 必填规则：协作类工单目标可选；目录事项绑 runbook 时目标**条件必填**（`_validate_runbook_intent` 校验）
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

### 13.3 前端对接规范

1. **表单永远选人/选物，不填 ID**：所有关联实体字段用可搜索选择器（remote-select），提交时仅发 id
2. 关联资源选择器数据源：`GET /api/v1/cmdb/resources/options?keyword=`（轻量字段：id/name/model_code/provider/region/status；名称+实例 ID 双模糊匹配）
3. 执行目标为**多选**选择器，写入 `target_resource_ids`；下拉项渲染 `name（model_code / region）`
4. 目录事项为下拉（数据源：/ticket-catalog，仅列二级事项）；处理组/处理人不在新建表单出现（自动派生/自动分派）
5. **处理组→处理人联动**（仅改派场景）：`POST /{id}/assign` 的人工改派弹窗用 `GET /ticket-groups/{id}/candidates`（组成员 ∪ 当日值班三线）列候选人；新建表单不出现处理人/处理组
6. 新建工单弹窗中，目录事项选中后若 `default_runbook_id` 非空 → 展示执行目标多选为必填；否则可选
7. 工单详情资源回显：按 `target_resource_ids` 批量调 `/cmdb/resources/{id}` 取 name 展示

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
