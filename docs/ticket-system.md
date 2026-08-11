# 工单系统（Tickets）设计文档

> 状态：v1 已实现 | 维护者：BingOps Team
> 相关代码：`bingops/models/ticket.py`、`bingops/services/ticket_service.py`、`bingops/api/v1/tickets.py`
> 数据库脚本：`sql/migrations/v4_tickets.sql`（已同步 `sql/schema.sql`）

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

- SLA 计时与超时预警：创建时设置 due_at，超时未处理自动标记
- 抄送/关注人：扩展工单可见与通知范围
- 统计报表：按状态/类型/处理人聚合的 dashboard 接口
- 附件支持：工单附件上传
- 审批流：change / data_ops 等高危类型的多级审批
