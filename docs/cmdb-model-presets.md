# CMDB 预置模型字段与关系约束清单

> 配合模型管理 UI 手动录入使用。与 `docs/cmdb-design.md` 配套。
>
> **字段录入完成（154 条），已录模型的字段定义以数据库
> `cmdb_model_fields` 为准，本文不再重复维护字段表**；只保留进度对账（最新 2026-08-08）、
> 剩余修正项、未建模型的字段表、关系约束录入清单和同步侧契约备忘。
>
> 录入总纪律（新建字段时仍然遵守）：
> 1. **通用层字段不要建**：`name / provider / provider_id / cloud_account / region / zone / status` 已是 `cmdb_resources` 表列，模型字段只放扩展属性；
> 2. **跨模型统一 code**：`cidr_block`、`memory_gb`、`rules` + `rules_hash`、`policy_*` 等在所有模型中保持同名，跨云审计依赖它们；
> 3. 预置字段勾选 `is_builtin`（防误删，同步写入依赖）；
> 4. 枚举字段一律**内联 options**，不使用公共选项库（决策 2026-07：复用率低、同步值由 consumer 归一化保证、现有前端可直接录入）；旧 3 个选项集已于 2026-08-08 清空，option-sets API 标 deprecated 休眠；
> 5. `fields` 中"同步保留"分组的 `*_id` 原始值字段是**孤儿认领**依据——建边失败时原始引用不丢，父资源到位后由 relationship_builder 反向补边。

---

## 1. 录入进度对账（2026-08-18 库内实测）

| 项 | 进度 | 说明 |
|-----|------|------|
| 分类 | 4 个 ✅ | 阿里云 / K8S / 谷歌云 / DNS；中间件分组用到再建 |
| 模型 | 33 个 | 待建：`k8s_ingress`（**模型已建 id=34，字段/关系待录**，见 §3.3；extractor/边分支已落地 2026-08-24）、`gcp_redis`（附录 A 规格就绪待录）；`apisix_route` 缓做（§3.1）；`selfhosted_*` 在用哪个建哪个（§3.2） |
| 字段 | 154 + 云模型批次 | 剩 2 见 §2；**字段疑惑对账见 §2.5** |
| 关系约束 | **53/56 已录** | 全部正确；余 3 条被 apisix_route 阻塞，见 §4 头部注 |
| 选项库 | 0 ✅ 已清空 | API 标 deprecated 休眠 |
| 实例/边 | 生产产出中 | K8s 链路已产出；**云链路第二批完成**（附录 B #23）；**GCP 链路启动**（附录 B #25）：compute/vpc/subnet/firewall 已实现；**DNS 链路完成**（附录 B #26）：dns_zone/dns_record 双厂商适配器 |
| 同步任务 | 多任务支持 | v8 迁移放开 (task_type, target_id) 唯一约束；消费端门控 = 启用任务并集（附录 B #24） |

---

## 2. 字段修正清单（剩 2 项，UI 上删掉重建）

> `field_type` 创建后不可改（后端有意设计，实例 fields JSONB 的值类型锤定），改类型一律**删掉重建**（现在无实例数据，零成本）。
> 其余 6 项（旧 #2/#3/#4/#5/#7/#8）已于 2026-08-08 对账确认修复。

| # | 模型.字段 | 现状 | 改为 | 原因 |
|---|-----------|------|------|------|
| 1 | aliyun_ecs.`memory_gb` | 类型=string | **删重建为 number** | 数值筛选/排序依赖数字类型 |
| 2 | dns_zone.`dns_servers` | string | **删重建为 json** | NS 服务器是数组 |

> 检查过无问题的项：全部枚举字段 options 与值域清单一致；EIP/CLB/云盘/NAS 的
> charge_type 只放 prepaid/postpaid（无 spot）是**正确裁剪**，这些资源不存在抢占式。

### 2.5 字段疑惑对账（2026-08-18，云链路落地后实测）

> 原则：采集器只填 API 能给的字段；给不了的不臆造（留空待人工）。疑似字段逐条裁决：

| # | 模型.字段 | 结论 | 理由 |
|---|-----------|------|------|
| 1 | aliyun_amqp.`support_node` | **建议删** | 预置期臆想字段：说明写「单节点/镜像/集群」却是 number 类型，自相矛盾；RabbitMQ OpenAPI（ListInstances/GetInstance，SDK 解包逐项核对）**不返回任何节点数**，产品规格只暴露系列+TPS/队列上限，节点拓扑云侧托管不可见 |
| 2 | aliyun_amqp.`port` | **建议删**（或留人工） | API 不返回；5671/5672 是协议常识非实例规格，采集器不填 |
| 3 | aliyun_rds.`connection_string` | **待决策** | 库内录的是单字段，附录 A 原设计是 private/public 一对；采集器现语义「内网默认、公网存在时覆盖」有歧义。二选一：接受现状并明确语义为「主连接地址」，或按附录 A 新增 `public_connection_string`（新增不违反不可变纪律） |
| 4 | aliyun_oss.`used_size_gb` | 保留，注明 | GetBucketStat 约小时级延迟，不做实时容量监控依据 |
| 5 | aliyun_nas.`used_size_gb` | 保留 | MeteredSize 实时计量，无延迟问题 |
| 6 | aliyun_amqp.`instance_type` | 保留（已修映射） | 官方值域 PROFESSIONAL/ENTERPRISE/VIP/SERVERLESS；采集器 VIP→platinum、SERVERLESS→serverless（模型枚举需 UI 补 serverless option）；Edition 是部署架构不作回退 |

---

## 3. 待建模型字段表

### 3.1 APISIX路由 `apisix_route`（K8S 分组）——**缓做**

> **决策反转（2026-08-20）**：ingress 仍在用，apisix 未铺开 → 流量入口跳先建 `k8s_ingress`，apisix_route 及其 3 条关系（#20/#40/#48）缓做。
> k8s_ingress 建模前置：cmdb-informer（Go）加 Ingress watch；bingops 侧字段规格/extractor/关系分支可先备。
> 原 APISIX 设计保留：流量链路第三跳 dns_record → NLB → APISIX(k8s_service) → apisix_route → 后端 k8s_service；
> 数据来源二选一（附录 B #10）：CRD 模式或 Admin API 模式，模型与关系两种模式通用。

| 字段名 | code | 类型 | 分组 | 必填 | 说明 |
|--------|------|------|------|------|------|
| 域名 | host | string | 路由配置 | 是 | 关系匹配锚点（hosts 多值存 json 或拆实例） |
| 路径 | paths | json | 路由配置 | 否 | uri/uris |
| 方法 | methods | json | 路由配置 | 否 | |
| 上游类型 | upstream_type | string | 上游配置 | 否 | k8s 服务发现 / 静态节点 |
| 上游引用 | upstream_ref | string | 同步保留 | 否 | service namespace/name 或节点列表，建边依据 |
| 启用插件 | plugins | json | 路由配置 | 否 | 插件名列表 + 关键配置摘要（限流/鉴权审计） |
| 原始配置 | raw | json | 同步保留 | 否 | Admin API / CRD 原样，兜底 |

### 3.2 中间件分组 `selfhosted_*`（自建，手动录入，在用哪个建哪个）

> 边界：云托管数据库在各云分组（附录 A）；自建的进本分组。
> 实例粒度 = **逻辑服务/集群级**（一套 3 节点 Kafka 是一个实例，broker 进 members）。
> `source='manual'`，云同步对账永不碰 manual 实例。
> 模型清单：`selfhosted_mysql` / `selfhosted_redis` / `selfhosted_kafka` / `selfhosted_es`…

通用字段模板（各引擎共用，个别引擎可加特有字段）：

| 字段名 | code | 类型 | 分组 | 必填 | 说明 |
|--------|------|------|------|------|------|
| 引擎版本 | version | string | 基础信息 | 是 | 版本审计（漏洞排查刚需） |
| 访问地址 | endpoint | string | 网络配置 | 是 | VIP/域名:端口 |
| 端口 | port | number | 网络配置 | 否 | |
| 部署形态 | deploy_mode | enum | 基础信息 | 是 | options：`[{"label":"虚机","value":"host"},{"label":"容器","value":"k8s"}]` |
| 架构模式 | ha_mode | string | 基础信息 | 否 | 单机/主从/哨兵/集群/Raft… |
| 成员节点 | members | json | 部署信息 | 否 | IP:port 列表，建承载边的依据 |

> 承载关系用 relates_to 不用 belongs_to（集群跨多台机器，挂从属树会重复显示），
> 见 §4 #50/#51。与 k8s_workload 不重复：workload 是运行形态，本分组是逻辑服务资产。

### 3.3 Ingress `k8s_ingress`（K8S 分组）——**模型已建，字段待录**

> 决策反转（2026-08-20）：ingress 在用 → 保留建模；cmdb-informer 已加 Ingress watch（2026-08-24 消息在流）。
> extractor 与关系分支已落地（附录 B #30）；字段/关系按下表 UI 录入后即生效。
> 关系：k8s_ingress → k8s_namespace belongs_to「命名空间归属」；k8s_ingress → k8s_service relates_to「路由上游」（backend service 名同 ns 匹配，service 事件反向重算）。

| 字段名 | code | 类型 | 分组 | 必填 | 说明 |
|--------|------|------|------|------|------|
| Ingress类 | ingress_class | string | 路由配置 | 否 | spec.ingressClassName |
| 域名 | hosts | json | 路由配置 | 是 | host 排序数组（关系/解析匹配锚点） |
| 路由规则 | rules | json | 路由配置 | 是 | `[{host,path,path_type,service_name,service_port}]` 排序保哈希稳定 |
| TLS域名 | tls_hosts | json | 路由配置 | 否 | spec.tls hosts 排序数组 |
| LB入口 | lb_ingress | json | 同步保留 | 否 | `[{ip,hostname}]`，status.loadBalancer，解析目标边二期锚点 |

### 3.4 不建模型的 Kind（纪律）

ReplicaSet、Endpoints、EndpointSlice、Event、Lease；
ConfigMap/Secret 待“配置影响面分析”立项再议。

---

## 4. 模型关系约束（cmdb_model_relations 录入清单，53/56）

> relation_type 只有 belongs_to（从属/树）与 relates_to（关联/图）两种，业务语义写在关系名。
>
> 录入进度（2026-08-19）：53 条全部正确（本轮新增 gcp_cloudsql/gcp_disk 4 条）；
> **阻塞 3**：#20/#40/#48 等 apisix_route 建好；#49/#50/#51 按原策略缓录。

### 4.1 从属关系（belongs_to）

> 映射比例按**源:目标**顺序读（与 4.2 一致）：`n:1` = n 个源实例挂同 1 个目标实例。
> 例：#1 = n 个 VPC 归属同一个账号；每个源实例只认一个父（树约束）。
> 录入方式：打开**源模型**的“关系定义”页→选目标模型→选类型→填关系名。
> “映射”列仅为语义注记（后端 cmdb_model_relations 无此字段，边表也不校验基数），无需录入；
> 它的真实用途是给 builder 当实现规约（如 1:1 匹配到多个候选时应告警而非连多条边）。

| # | 源模型（child） | 目标模型（parent） | 映射 | 关系名 |
|---|----------------|-------------------|------|--------|
| 1 | aliyun_vpc | aliyun_account | n:1 | 账号归属 |
| 2 | aliyun_eip | aliyun_account | n:1 | 账号归属（EIP 是账号级资源） |
| 3 | aliyun_disk | aliyun_account | n:1 | 账号归属（游离盘无实例父，挂账号） |
| 4 | aliyun_nas | aliyun_account | n:1 | 账号归属 |
| 5 | aliyun_vswitch | aliyun_vpc | n:1 | 网络归属 |
| 6 | aliyun_security_group | aliyun_vpc | n:1 | 网络归属 |
| 7 | aliyun_clb | aliyun_vpc | n:1 | 网络归属 |
| 8 | aliyun_nlb | aliyun_vpc | n:1 | 网络归属 |
| 9 | aliyun_nat_gateway | aliyun_vpc | n:1 | 网络归属 |
| 10 | aliyun_ecs | aliyun_vswitch | n:1 | 部署于 |
| 11 | gcp_vpc | gcp_account | n:1 | 项目归属 |
| 12 | gcp_subnet | gcp_vpc | n:1 | 网络归属 |
| 13 | gcp_firewall | gcp_vpc | 1:1 | 防火墙归属（合成实例，每 VPC 仅一个） |
| 14 | gcp_compute | gcp_subnet | n:1 | 部署于 |
| 15 | k8s_cluster | aliyun_vpc | n:1 | 部署于（ACK） |
| 16 | k8s_cluster | gcp_vpc | n:1 | 部署于（GKE） |
| 17 | k8s_namespace | k8s_cluster | n:1 | 集群归属 |
| 18 | k8s_node | k8s_cluster | n:1 | 集群归属 |
| 19 | k8s_pv | k8s_cluster | n:1 | 集群归属（PV 是集群级资源） |
| 20 | apisix_route | k8s_cluster | n:1 | 网关归属（Admin API 模式无 namespace，统一挂集群） |
| 21 | k8s_workload | k8s_namespace | n:1 | 命名空间归属 |
| 22 | k8s_service | k8s_namespace | n:1 | 命名空间归属 |
| 23 | k8s_pvc | k8s_namespace | n:1 | 命名空间归属 |
| 24 | k8s_pod | k8s_workload | n:1 | 属主负载 |
| 25 | k8s_pod | k8s_node | n:1 | 调度于 |
| 26 | k8s_pod | k8s_namespace | n:1 | 命名空间归属（裸 Pod 兜底） |
| 27 | dns_record | dns_zone | n:1 | 域归属 |
| 52 | aliyun_oss | aliyun_account | n:1 | 账号归属 |
| 53 | gcp_cloudsql | gcp_vpc | n:1 | 网络归属 |
| 54 | gcp_disk | gcp_account | n:1 | 账号归属（游离盘无实例父，挂账号） |

### 4.2 关联关系（relates_to）

| # | 源模型 | 目标模型 | 映射 | 关系名 |
|---|--------|---------|------|--------|
| 28 | aliyun_ecs | aliyun_security_group | n:n | 绑定安全组 |
| 29 | aliyun_clb | aliyun_ecs | n:n | 负载均衡后端 |
| 30 | aliyun_nlb | aliyun_ecs | n:n | 服务器组后端 |
| 31 | aliyun_eip | aliyun_ecs | n:n | 绑定 / DNAT 暴露（kind 区分） |
| 32 | aliyun_nat_gateway | aliyun_eip | 1:n | 绑定 EIP |
| 33 | aliyun_disk | aliyun_ecs | n:1 | 挂载于（instance_id 匹配） |
| 34 | aliyun_nas | aliyun_vpc | n:n | 挂载点（mount_targets 的 vpc_id 匹配） |
| 35 | k8s_node | aliyun_ecs | 1:1 | 承载于（IP / instance_id 匹配） |
| 36 | k8s_node | gcp_compute | 1:1 | 承载于 |
| 37 | k8s_service | k8s_pod | n:n | selector 匹配 |
| 38 | k8s_service | aliyun_clb | 1:1 | LB 桥接（按 IP 匹配 lb_ingress） |
| 39 | k8s_service | aliyun_nlb | 1:1 | LB 桥接（按 hostname 匹配） |
| 40 | apisix_route | k8s_service | n:n | 路由上游（upstream_ref 匹配） |
| 41 | k8s_pod | k8s_pvc | n:n | 使用存储（pod.spec.volumes 提取） |
| 42 | k8s_pvc | k8s_pv | 1:1 | 绑定（volume_name 匹配） |
| 43 | k8s_pv | aliyun_disk | 1:1 | CSI 桥接（volume_handle = DiskId，kind=csi） |
| 44 | k8s_pv | aliyun_nas | n:1 | CSI 桥接（volume_handle 解析文件系统 ID） |
| 57 | k8s_ingress | k8s_namespace | n:1 | 命名空间归属 |
| 58 | k8s_ingress | k8s_service | n:n | 路由上游（backend service 名匹配） |
| 55 | gcp_disk | gcp_compute | n:n | 挂载于（users 列表匹配） |
| 56 | k8s_pv | gcp_disk | 1:1 | CSI 桥接（kind=csi） |
| 45 | dns_record | aliyun_eip | n:n | 解析目标（A 记录按 IP） |
| 46 | dns_record | aliyun_clb | n:n | 解析目标（A 记录按 IP） |
| 47 | dns_record | aliyun_nlb | n:n | 解析目标（CNAME 按 hostname） |
| 48 | dns_record | apisix_route | n:n | 解析入口（route.host 与 FQDN 精确匹配） |
| 49 | dns_zone | aliyun_vpc | n:n | 私有域绑定（用到 PrivateZone 再录） |
| 50 | selfhosted_* | aliyun_ecs / gcp_compute | n:n | 部署于（members IP 匹配，手动为主） |
| 51 | selfhosted_* | k8s_workload | n:1 | 部署于（K8s 形态） |

---

## 附录 A：按需补建模型的字段（在用再录）

> charge_type 的 options 与已录模型一致：
> `[{"label":"包年包月","value":"prepaid"},{"label":"按量付费","value":"postpaid"}]`。
> 分组沿用已录模型约定：基础信息 / 网络配置 / 计费信息 / 同步保留。
> 分组名若与库内已录模型不一致，**以库内同名模型的 group_name 为准**。

### aliyun_rds

| 字段名 | code | 类型 | 分组 | 必填 | 说明 |
|--------|------|------|------|------|------|
| 数据库引擎 | engine | string | 基础信息 | 是 | MySQL / PostgreSQL / SQLServer |
| 引擎版本 | engine_version | string | 基础信息 | 是 | 版本审计 |
| 实例规格 | instance_class | string | 基础信息 | 是 | |
| 存储容量(GB) | storage_gb | number | 基础信息 | 是 | |
| 内网连接地址 | private_connection_string | string | 网络配置 | 否 | 命名对齐 gcp_cloudsql 的 private_ip/public_ip |
| 公网连接地址 | public_connection_string | string | 网络配置 | 否 | 公网暴露面审计依据，未开通则不填 |
| 端口 | port | number | 网络配置 | 否 | 内外网通常同端口 |
| 付费类型 | charge_type | enum | 计费信息 | 否 | options 同已录模型 |
| 到期时间 | expired_at | date | 计费信息 | 否 | 包年包月续费提醒依据 |
| VSwitch ID | vswitch_id | string | 同步保留 | 否 | 建边依据（孤儿认领） |

> 采集契约：主接口 `DescribeDBInstances`/`DescribeDBInstanceAttribute` **不返回连接地址**（后者仅含内网
> ConnectionString）；内外网地址均需逐实例调 `DescribeDBInstanceNetInfo`，按 `NetType=Private/Public`
> 拆分填充（同 cloud-sync-design §7 ACK enrichment 的二次调用模式）。实例规模小 + 30min 档，N+1 可接受；
> 若后续规模变大，降为仅对内容哈希变化的实例补调。

从属：aliyun_rds belongs_to aliyun_vswitch (n:1，网络归属)。

### aliyun_redis

| 字段名 | code | 类型 | 分组 | 必填 | 说明 |
|--------|------|------|------|------|------|
| 引擎版本 | engine_version | string | 基础信息 | 是 | 版本审计 |
| 实例规格 | instance_class | string | 基础信息 | 是 | |
| 容量(MB) | capacity_mb | number | 基础信息 | 是 | |
| 连接地址 | connection_string | string | 网络配置 | 否 | |
| 端口 | port | number | 网络配置 | 否 | |
| VSwitch ID | vswitch_id | string | 同步保留 | 否 | 建边依据（孤儿认领） |

从属：aliyun_redis belongs_to aliyun_vswitch (n:1，网络归属)。

### aliyun_amqp（阿里云 RabbitMQ）

| 字段名 | code | 类型 | 分组 | 必填 | 说明 |
|--------|------|------|------|------|------|
| 实例系列 | instance_type | enum | 基础信息 | 是 | options：`[{"label":"专业版","value":"professional"},{"label":"企业版","value":"enterprise"},{"label":"铂金版","value":"platinum"},{"label":"Serverless版","value":"serverless"}]`（**serverless option 待 UI 补录**；API 值域 PROFESSIONAL/ENTERPRISE/VIP/SERVERLESS，采集器 VIP→platinum 映射） |
| ~~节点数~~ | ~~support_node~~ | - | - | - | **建议删**（§2.5 #1：API 不返回，臆想字段） |
| 队列上限 | max_queues | number | 基础信息 | 否 | 规格容量参考 |
| TPS 上限 | max_tps | number | 基础信息 | 否 | 规格容量参考 |
| 接入点 | endpoint | string | 网络配置 | 否 | AMQP 接入地址 |
| ~~端口~~ | ~~port~~ | - | - | - | **建议删**（§2.5 #2：API 不返回） |
| 付费类型 | charge_type | enum | 计费信息 | 否 | options 同已录模型 |
| 到期时间 | expired_at | date | 计费信息 | 否 | 包年包月续费提醒依据 |
| VSwitch ID | vswitch_id | string | 同步保留 | 否 | 建边依据（孤儿认领） |

从属：aliyun_amqp belongs_to aliyun_vswitch (n:1，网络归属)。
> 采集 API：AMQP OpenAPI `ListInstances`；实例级同步，vhost/queue 不建模（粒度过细、churn 高，同不建 ReplicaSet 的纪律）。

### gcp_cloudsql ✅ 已录

| 字段名 | code | 类型 | 分组 | 必填 | 说明 |
|--------|------|------|------|------|------|
| 数据库引擎 | engine | string | 基础信息 | 是 | MYSQL / POSTGRES / SQLSERVER |
| 引擎版本 | engine_version | string | 基础信息 | 是 | 版本审计 |
| 机器规格 | tier | string | 基础信息 | 是 | |
| 存储容量(GB) | storage_gb | number | 基础信息 | 是 | |
| 内网 IP | private_ip | string | 网络配置 | 否 | |
| 公网 IP | public_ip | string | 网络配置 | 否 | |
| VPC ID | vpc_id | string | 同步保留 | 否 | 建边依据（孤儿认领） |

从属：gcp_cloudsql belongs_to gcp_vpc (n:1，网络归属)。

### gcp_redis（Memorystore for Redis）

| 字段名 | code | 类型 | 分组 | 必填 | 说明 |
|--------|------|------|------|------|------|
| 引擎版本 | engine_version | string | 基础信息 | 是 | redisVersion `REDIS_7_2` → 存 `7.2`（剥前缀下划线转点） |
| 部署形态 | tier | enum | 基础信息 | 是 | options：`[{"label":"基础版","value":"BASIC"},{"label":"高可用","value":"STANDARD_HA"}]` |
| 容量(MB) | capacity_mb | number | 基础信息 | 是 | memorySizeGb×1024，**对齐 aliyun_redis 同 code** |
| 连接地址 | connection_string | string | 网络配置 | 否 | host（仅内网 IP；Memorystore 不暴露公网） |
| 端口 | port | number | 网络配置 | 否 | 默认 6379 |
| VPC ID | vpc_id | string | 同步保留 | 否 | authorizedNetwork（VPC 名称），建边依据（孤儿认领） |

从属：gcp_redis belongs_to gcp_vpc (n:1，网络归属)。
> 采集 API：Cloud Redis `projects.locations.instances.list`（location 用 `-` 一次拉全）；
> state 映射 READY→running / CREATING/UPDATING/DELETING→maintenance / SUSPENDED→stopped；
> OAuth scope 需补 `cloud-platform.read-only`（compute.readonly 不覆盖 Redis API）。

### gcp_disk ✅ 已录（GKE 跑有状态服务、需 PV 桥接对端）

| 字段名 | code | 类型 | 分组 | 必填 | 说明 |
|--------|------|------|------|------|------|
| 磁盘类型 | disk_type | string | 基础信息 | 是 | pd-ssd / pd-balanced |
| 容量(GB) | size_gb | number | 基础信息 | 是 | |
| 是否加密 | encrypted | boolean | 基础信息 | 否 | |
| 挂载实例 | users | json | 同步保留 | 否 | 挂载实例列表，建边依据 |

从属：gcp_disk belongs_to gcp_account (n:1，账号归属，游离盘无实例父)；
关联：gcp_disk relates_to gcp_compute (n:n，挂载于)、k8s_pv relates_to gcp_disk (1:1，CSI 桥接 kind=csi)。

---

## 附录 B：同步侧待办备忘（与本清单联动）

| # | 事项 | 说明 |
|---|------|------|
| 1 | ~~`cmdb_relates_to` 加 `kind` 列~~ **✅ 已完成** | v9 迁移：kind VARCHAR(32) 默认空串，唯一键改 `UNIQUE(source_id, target_id, kind)`；存量边语义不变 |
| 2 | Kind → model_code 映射更新 | Deployment/StatefulSet/DaemonSet → k8s_workload（写 fields.workload_type）；不 Watch RS/Endpoints/EndpointSlice |
| 3 | Pod 属主两级解析 | ownerReferences Pod→RS→Deployment，直接建 pod belongs_to workload。**已确认 informer 不 Watch ReplicaSet**：消费端用 RS 名去掉末段 pod-template-hash 后缀得到 Deployment 名，无需 Go 侧改动 |
| 4 | Service↔Pod 双向触发 | Service 事件与 Pod 事件都要重算 selector 匹配边 |
| 5 | 孤儿认领 | 子资源先到时只落库不建边，父资源到位后按"同步保留"字段反向补边 |
| 6 | 网络资源降频采集 | VPC/子网/SG/防火墙 1h 级；主机 5–10min 级；DNS 30min–1h 全量对账 |
| 7 | GCP 防火墙级联删除 | 删 gcp_vpc 时软删对应 gcp_firewall 合成实例 |
| 8 | 规则/条目级 diff | rules_hash（SG/防火墙）、snat_hash/dnat_hash（NAT）不变则跳过 change_log，变更按 rule_id/entry_id 对齐输出 |
| 9 | ~~Informer 新增 Watch Kind~~ **已确认无需开发** | informer 已支持 13 种资源（含 persistentvolumes/persistentvolumeclaims），配置启用即可；endpoints 虽支持但纪律是不建模——配置里不启用；不支持 Ingress（与不建 Ingress 的决策一致） |
| 10 | APISIX 采集通道二选一 | CRD 模式：Informer 加 Watch ApisixRoute/ApisixUpstream，走现有 Topic；Admin API 模式：轻量拉取器 5–10min，只读 Key，凭据不进 git |
| 11 | ~~云采集器新增存储 API~~ **✅ 已完成** | DescribeDisks（含游离盘）/ DescribeFileSystems + 挂载点已落地（aliyun_disk / aliyun_nas 适配器）；降频按任务拆分实现（附录 B #24） |
| 12 | ~~builder 新增桥接/派生规则~~ **部分完成** | 已落地：node→云主机承载于（#27）、**NAT DNAT 派生边**（dnat.public_ip→EIP.ip_address、dnat.private_ip→ECS.private_ip，kind=dnat 描述「DNAT 暴露」，管理范围限定本 NAT eip_ids）；EIP 直绑边 kind=bind。未决：volume_handle→disk/nas CSI 桥接、apisix/dns 相关（依赖 #10/#48） |
| 13 | ~~应用关联物化（后端表，非模型）~~ **✅ 已完成** | v10 迁移 cmdb_app_resources（UNIQUE(app_id,resource_id)，双 FK CASCADE）；source='tag' 由两个消费端标签同步后 refresh_app_links_from_tags 重算（仅服务级 CI：workload/service/rds/redis/amqp/clb/nlb/cloudsql/redis-gcp；app 键含 k8s:app）；source='manual' API 绑定/解绑/双向查询（/apps/{id}/resources、/apps/by-resource/{rid}） |
| 14 | **K8s 消息契约以 cmdb-informer 为准重写** | 实测对比（`cmdb-informer/internal/message/types.go` 的 `MQMessage` vs `kafka_messages.py` 的 `K8sResourceMessage`）两边结构性不一致：字段名（cluster_id vs cluster、resource_type vs kind）、消息结构（Go 嵌套 `resource.{uid,name,labels,annotations,spec,status,raw}` vs Python 平铺）、Python 缺 `message_id`（幂等去重）/`sync_type`/`snapshot` 事件/`old_resource`。重构时照 Go 侧 `MQMessage` 重写 Python schema，Go 生产者不动；**已确认** `resource_type` 取值为小写复数（pods/services/deployments/statefulsets/daemonsets/nodes/namespaces/persistentvolumes/persistentvolumeclaims 等），消费端映射表按此格式编写 |
| 15 | ~~快照对账删除（依赖 #14）~~ **✅ 已完成** | 消费端快照会话机制：full_sync/periodic_sync 消息累加可见集（seen 记录在 upsert 跳过路径之前），空闲 120s 视为轮终（懒终结 + 60s 扫尾任务双保险），covered 模型差集软删 + 清边 + 记 delete 审计；仅对账快照覆盖的模型（k8s_cluster 等自动创建资源不误伤）；顺带 message_id 有界去重防 Kafka 重放 |
| 16 | 消费链路 v1→v2 重构（前置：模型/字段/约束录入完成）【K8s 链路 ✅ 已完成，云链路待第二批】 | 已落地：schema 照 Go `MQMessage` 重写；`k8s_extractors.py` 按 resource_type→model_code→model_id 提取 fields（deployments/statefulsets/daemonsets 归一 k8s_workload，configmaps/secrets 跳过）；labels 差异同步 source='cloud'；builder 按库内 12 条 K8s 约束建边（description=约束 relation_name），从属边整包替换、关联边按语义槽位替换；软删除同步清边；集群实例兜底创建（#17）；pod 边不记 change_log（#21）。跨云桥接边（#37/#38/#40/#41/#46/#47/#15/#16）留待云链路重构；#15 快照差集软删未实现（依赖消息序识别，后续迭代） |
| 17 | k8s_cluster 实例自动 upsert | 集群本身无 Watch 事件，但是建边根节点：消费端首次见到某 `cluster_id` 时自动创建 k8s_cluster 实例（cluster_type 落 fields，provider 按 #19 映射托管厂商 ack→aliyun/gke→gcp/自建→k8s），**子资源继承集群 provider**（集群实例是厂商唯一事实源）；无需手录、无需 informer 发消息 |
| 18 | informer 侧零开发确认 | 建模所需 9 种 Kind（nodes/namespaces/pods/services/deployments/statefulsets/daemonsets/PV/PVC）informer 已全部支持，只需改 config.yaml 资源清单+namespace 白名单；唯一例外是 #10 选 CRD 模式时需加 Watch ApisixRoute/ApisixUpstream |
| 19 | 合成 ID / 单位换算规则汇总（consumer 实现时照抄） | `gcp_firewall.provider_id = fw:{project_id}:{vpc_name}`（每 VPC 一个合成实例）；`dns_record.provider_id`：阿里云用 RecordId，GCP 合成 `{zone}:{name}:{type}:{policy_key或value}`；`aliyun_oss.provider_id = Bucket 名`；`dns_record.name = FQDN`（@ 存裸域名，泛解析存 `*.example.com`）；`memory_gb = API 返回的 MB / 1024`（ECS/GCE，nano 规格存 0.5）；`k8s_cluster.provider` 标托管厂商（aliyun/gcp），是云与容器两个世界的桥 |
| 20 | 实例关系 API 单父校验 | 手工创建 belongs_to 边时 service 层校验：该 child 已有父则拒绝（或提示替换）；边表现状只有 UNIQUE(child_id, parent_id) 防重复边，不防一子多父 |
| 21 | 高 churn Kind（pod）边写入策略 | pod 边变更**不写 change_log**（discovery 数据审计价值≈0，防噪音淹没）；pod 删除靠边表外键 ON DELETE CASCADE 自动级联，无需主动删边；builder 对 pod 按事件增量 upsert，禁止全量重建（防死元组/VACUUM 压力） |
| 22 | **ReplicaSet 层不建模**（决策 2026-08-11） | pod ownerRef 实际经过 RS 层，但**边扁平化为 pod→workload**（RS 名剥离尾部 `-hash` 定位 Deployment，kubectl 同款做法）；RS 痕迹保留在 `pod.owner_kind/owner_name` 字段供排障追溯。不建模理由：RS 无独立管理价值（完全被 Deployment 掌控）、每次发版新建 RS 旧 RS 僵尸留存（churn 翻倍）、informer 未 watch replicasets。发版拓扑（新旧 RS 共存）属运行时视图，归 kubectl/控制台 |
| 23 | **云链路第二批完成**（2026-08-18） | 采集器 15 类 fetcher 全部落地：account（配置驱动零 API，建树根）/ ecs / vpc / vswitch / security_group / eip / clb / nlb / nat_gateway / oss / disk / nas / rds / redis / amqp；消费端边重建分支对齐库内关系约束描述（网络归属/账号归属/挂载于/绑定 EIP/挂载点/负载均衡后端/服务器组后端）；默认集派生自 _FETCHERS（空白名单 = 全部已实现，未实现类型不再进默认集）；SDK 方法名/响应结构以解包 wheel 核对为准（防 AttributeError 类事故）；未决：k8s_pv→disk/nas CSI 桥接、NAT DNAT 派生边（依赖 #1 kind 列）、dns_record 解析目标边 |
| 24 | **同步任务多目标拆分**（v8 迁移，2026-08-18） | 删除 (task_type, target_id) 唯一约束：同一云账号可按资源类型拆多个任务独立调度（如计算 5min / 网络 1h）；消费端门控改为「任一启用任务白名单命中即放行」（空=全部）；拆分建议白名单互不重叠避免重复采集；同账号多任务可并发，cron 错开防限流 |
| 25 | **GCP 链路启动**（2026-08-19） | gcp_compute 生产跑通 + gcp_vpc/gcp_subnet/gcp_firewall 已实现。关键事实：① 大陆出网不通 googleapis.com，采集器部署配 HTTPS_PROXY/NO_PROXY=.aliyuncs.com 仅代理 GCP 流量；② **OAuth scope 必须 compute.readonly**（cloud-platform.read-only 会被 Compute API 拒 403），DNS 另需 ndev.clouddns.readonly（多 scope token 两边都满足）；③ OS 三级推断：licenses 家族项目 slug→许可证名关键词→Disks.get 磁盘 sourceImage；④ GCP 名称型 provider_id 约定（vpc/subnet 用名称，compute 用数字 ID）；⑤ 待办：gcp_disk / gcp_cloudsql / gcp_account 根节点 |
| 26 | **DNS 链路完成**（2026-08-19） | dns_zone/dns_record 跨厂商共用模型 code，aliyun（alidns：DescribeDomains/DescribeDomainRecords）与 gcp（Cloud DNS：ManagedZones/ResourceRecordSets）双适配器落地。归一化约定照附录 B #19：name 存 FQDN（@→裸域、*→*.zone、GCP 尾点剥离）；aliyun provider_id=RecordId、gcp 合成 {zone}:{fqdn}:{type}:{value}；MX/SRV 优先级 GCP 嵌 rrdatas 需拆分、TXT 去引号；aliyun Line→policy_type simple/line+policy_key；raw json 保留原始数据；SOA 不建；GCP zone_type 取 visibility（public/private）。record→zone belongs_to「域归属」消费端已接；**未决**：解析目标边（#45-47，A 按 IP 匹配 EIP、CNAME 按 hostname 匹配 CLB/NLB）二期；PrivateZone（pvtz）用到再录 |
| 27 | **K8s 节点↔云主机桥接边完成**（2026-08-19） | #35/#36 承载于：节点 upsert 时按 fields.instance_id 精确匹配（ACK providerID 解析 i-xxx=ECS provider_id；GKE gce://proj/zone/name 解析实例名=gcp_compute.name）优先、internal_ip==private_ip 兜底；云主机 upsert 时反向孤儿认领（adopt_node_host_edges）补边；槽位整包替换 diff 跳过；自建集群（provider=k8s）无对端静默跳过 |
| 28 | **K8s 资源地域消费端推导**（2026-08-21） | informer 契约不改；消费端从 node 消息 labels 提取 `topology.kubernetes.io/region/zone` 写 k8s_cluster 实例 region/zone 通用列（集群实例为地域唯一事实源，同 #17/#19 模式；仅首次识别写入，防多 zone 集群 zone 值抖动）；所有 K8s 资源 upsert 时继承集群 region/zone；region 首次识别时批量回填该集群 discovery 资源历史空白地域（无变更跳过分支不自愈 region）；标签缺失（自建集群无 cloud-provider）优雅降级留空。ACK 实测 `cn-guangzhou`/`cn-guangzhou-a|b`，与云侧表达一致；GKE 待接入后验收 |
| 29 | **gcp_disk + gcp_account 根节点完成**（2026-08-24） | disk.py：DisksClient.aggregated_list（scope zones/{zone}，zone→region 地图过滤 accounts.yaml regions）；provider_id={zone}/{name}（盘名仅 zone 内唯一，同 subnet 撞键教训）；字段 disk_type=type_ URL 末段（proto 属性带下划线）/size_gb/encrypted=三类加密钥存在性/users=[{name,zone}]（实例 URL 解析）；labels→cloud_tags。account.py：零 API 配置驱动根节点（同 aliyun account），provider_id=项目 ID，顺带修复 gcp_vpc 账号归属悬空边。消费端：gcp_disk→gcp_account 账号归属 + _rebuild_gcp_disk_edges 挂载于（users name+zone+账号 精确匹配 gcp_compute，GCE 名仅 zone 内唯一）；k8s_pv→gcp_disk CSI 桥接仍待办 |
| 30 | **k8s_ingress 消费端落地**（2026-08-24） | informer 已发 ingresses 消息；k8s_extractors 加 _extract_ingress（rules/hosts/tls_hosts/lb_ingress 归一化排序，_backend_services 下划线内部键供建边）；relationship_builder：ingress→namespace 命名空间归属 + _rebuild_ingress_service_edges 路由上游（backend 名按 provider_id {cluster}/{ns}/{name} 精确匹配）+ service 事件反向重算引用它的 ingress（_rebuild_ingress_inbound_edges）。模型字段/关系待 UI 录入（§3.3）；字段未录期间 _backend_services 仍存活（下划线键豁免过滤），路由上游边先行可用 |
| 31 | **gcp_cloudsql + gcp_redis 完成**（2026-08-24） | cloudsql.py：PyPI 无 sqladmin GAPIC 包（google-cloud-sqladmin-* 不存在）→ SQL Admin REST（AuthorizedSession，requests 系走代理）+ pageToken 分页；databaseVersion MYSQL_8_0 拆 engine/engine_version；ipAddresses PRIMARY/PRIVATE 拆公网/内网；settings.ipConfiguration.privateNetwork→vpc_id+parent。redis.py：google-cloud-redis GAPIC（list_instances locations='-' 全项目）；provider_id={region}/{name}（名字仅 location 内唯一）；redisVersion REDIS_7_2→7.2；memorySizeGb×1024=capacity_mb；authorized_network→vpc_id+parent。scope 加 cloud-platform（Memorystore/SQL Admin 无专属只读 scope，IAM 仍限只读）。消费端两者复用 _rebuild_parent_edge(DESC_NETWORK_BELONG)。**gcp_redis 模型待补录**：connection_string/port/vpc_id 三字段 + gcp_redis→gcp_vpc 网络归属关系（已录部分：engine_version/tier/capacity_mb） |
