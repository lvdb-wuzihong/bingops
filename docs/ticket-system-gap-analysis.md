# SRE 工单系统差异分析：Gemini 蓝图 vs BingOps v1 现状

> 输入：Gemini 描述的 SRE 工单系统（设计理念 + 五层架构 + 生命周期 + 度量体系 + 最佳实践）
> 基线：`docs/ticket-system.md`（BingOps tickets v1，已实现）
> 结论先行：**两者不冲突，是同一系统的两个演进阶段**。v1 是"协作与流转"地基，Gemini 蓝图是"自动执行平台"目标态。核心差距在于**执行引擎**与**风控层**，而非工单模型本身。

## 1. 定位差异

| 维度 | BingOps v1（现状） | Gemini 蓝图（目标态） |
|------|-------------------|----------------------|
| 工单本质 | 人工协作的流转记录 | 变更执行的载体与风控入口 |
| 状态语义 | 人的处理进度（open→resolved） | 执行生命周期（审批→预检→执行→校验） |
| 终态判定 | 人工标记 resolved/closed | 系统校验（指标对比、健康检查） |
| 自动化角色 | 无（纯记录） | 核心（80% 工单填单即执行） |

**关键判断**：v1 的工单模型（主表 + 流转记录 + 状态矩阵 + RBAC）可作为执行型工单的"申请与审计外壳"保留，执行能力应作为独立子系统（jobs）挂接，而不是塞进 tickets 表。

## 2. 逐项对照

### 2.1 已覆盖（v1 可直接复用）

| Gemini 能力 | v1 对应实现 | 备注 |
|-------------|------------|------|
| 工单生命周期：提交、归档 | 创建 + 状态流转 + 终态管理 | 覆盖 6 阶段中的第 1、6 阶段 |
| 审批人/处理人路由 | assignee 指派 + `ticket:assign` 权限 | 缺审批链，只有单点处理人 |
| RBAC 权限控制 | `ticket:*` 五权限码接入预置角色 | 蓝图要求的 RBAC/ABAC 审批策略的权限基础 |
| 全操作审计时间线 | `ticket_comments` 不可变流转表 | 审批、预检、执行日志未来都可作为新 action 类型落入 |
| CMDB 集成 | `related_resource_id` 关联资源 | 蓝图"提交时 CMDB 校验""归档时同步 CMDB"的挂接点 |
| 优先级 | `priority`（low~urgent） | 可承载风险等级展示，但缺自动评级 |

### 2.2 部分覆盖（需扩展）

| Gemini 能力 | 现状 | 扩展方向 | 成本 |
|-------------|------|----------|------|
| 状态机引擎 | 有状态矩阵（协作态） | 扩展执行态：`pending_approval → scheduled → executing → verifying → completed/failed/rolled_back`；可按 ticket_type 选用不同状态机 | 中 |
| 风险评级（P0-P4） | 无 | 新增 `risk_level` 字段 + 评级规则（环境=生产、类型=data_ops 等条件加权） | 低 |
| 服务目录/动态表单 | ticket_type 静态枚举 + 纯文本 description | 利用 PostgreSQL JSONB：服务目录表存 JSON Schema，工单参数落 `params JSONB` | 中 |
| 度量体系 | 有 created_at/resolved_at/closed_at 时间戳 | 加 `first_response_at`、`due_at` 即可算 MTTA/MTTR；Toil 统计基于 ticket_type + action 聚合 | 低 |
| CMDB 闭环归档 | 可关联资源 | 执行完成后回调写 CMDB（复用现有 resource service） | 中 |

### 2.3 完全缺失（需新建）

| Gemini 能力 | 缺失内容 | 依赖条件 | 成本 |
|-------------|----------|----------|------|
| 审批流引擎 | 审批链表、双签、自动直通、Emergency 绿色通道（先执行后补单） | 仅后端 | 中 |
| 变更风控栅栏 | Change Freeze 窗口表、高危命令拦截、同资源并发控制 | 仅后端 | 中 |
| ChatOps（IM 机器人） | 飞书审批/通知/一键操作 | 项目已有 `core/feishu_provider.py`（SSO），可扩展消息能力 | 中 |
| 执行引擎（Job Executor） | 任务模板、Runner、预检/后置校验、灰度、幂等、回滚、健康观察 | 需对接 Ansible/Terraform/K8s API/监控 | **高** |
| 集成：监控/CI-CD/GitOps | Prometheus 联动、变更 Annotate | 需监控系统先行 | 高 |
| Toil/ROI 看板 | 自动化率、Top 10 待自动化场景 | 依赖度量数据积累 | 低（数据齐后） |

### 2.4 与 BingOps 现状冲突或不适用的点

1. **"填单即执行"的前提缺失**：蓝图假设底层有 Ansible/Terraform/GitOps 标准接口可调，BingOps 目前没有任何执行通道（无 Agent、无 Runner、无 CI/CD）。这是最大前置依赖，跳过它谈自动工单是空中楼阁。
2. **健康检查联动 Prometheus/Datadog**：平台尚无监控体系接入，该能力需排在监控集成之后。
3. **多 IM 打通（企微/钉钉/Slack）**：过度设计，BingOps 已绑定飞书（SSO provider 已存在），只做飞书即可。
4. **ABAC 动态审批策略**：当前 RBAC 粒度足够，ABAC 等审批规则复杂后再引入，避免提前造规则引擎。

## 3. 推荐演进路线

### Phase A：协作与审批（纯后端，1~2 个迭代）

1. **ticket_type 扩为 9 类**（resource request/change/incident/access/troubleshooting/security/data_ops/maintenance/general）——仅改服务层常量，零迁移
2. **审批流**：新增 `ticket_approvals` 表（审批人、动作、意见、时间），按 ticket_type + risk_level 配置审批链；低风险自动直通
3. **风险评级**：`tickets.risk_level` 字段 + 规则化评级（生产环境/高危类型提级）
4. **SLA 基础**：`due_at`、`first_response_at` 字段 + MTTA/MTTR 聚合接口
5. **变更封禁窗口**：`change_freezes` 表（范围、起止时间），change 类工单创建/执行前校验

### Phase B：通知与轻自动化（依赖飞书开放平台）

1. 飞书机器人：工单创建/待审批/超时通知
2. IM 卡片一键审批、审批结果回写
3. Emergency 绿色通道：incident 类支持"先执行后补单"标记

### Phase C：执行引擎（独立子系统 `jobs`，重大投入）

1. 任务模板（JSON Schema 定义参数 + 执行通道 + 预检/回滚步骤）
2. Job Runner + 状态机执行态接入 tickets（工单批准后生成 Job）
3. 预检/后置校验、并发控制（同资源单工单执行锁）
4. 对接顺序建议：K8s API（已有 Informer 采集经验）→ Ansible → Terraform
5. 监控联动与自动回滚（依赖监控系统就位）

### 每个阶段的验收锚点（呼应蓝图度量目标）

- Phase A 后：可统计 MTTA/MTTR、审批自动化率
- Phase B 后：IM 介入率、平均审批时长
- Phase C 后：Toil 占比、自动执行率（目标 80%）、变更相关故障率

## 4. 结论

| 判断 | 说明 |
|------|------|
| v1 不需要推倒重来 | 工单主表、流转记录、RBAC、状态矩阵全部保留，作为申请与审计外壳 |
| 蓝图的价值在排序 | Gemini 给的是目标态全景，落地必须按"审批 → 通知 → 执行"递进，不能跳级 |
| 最大的坑 | 直接照蓝图造执行引擎——没有执行通道（Ansible/K8s API/Terraform）之前，所有"自动化"都是模拟 |
| 立即可做的第一步 | Phase A 的 5 项均为纯后端扩展，与现有三层架构完全兼容 |
