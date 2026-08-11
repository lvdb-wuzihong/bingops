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

## 1. 录入进度对账（2026-08-08 库内实测）

| 项 | 进度 | 说明 |
|-----|------|------|
| 分类 | 4 个 ✅ | 阿里云 / K8S / 谷歌云 / DNS；中间件分组用到再建 |
| 模型 | 28 个 | 待建：`apisix_route`（§3.1）；**待删：`k8s_ingress`（id=34，0 字段，决策不建）**；`selfhosted_*` 在用哪个建哪个（§3.2） |
| 字段 | **154 条已录** | 8 处偏差已修 6，剩 2 见 §2 |
| 关系约束 | **46/52 已录** | 46 条全部正确；余 3 条被 apisix_route 阻塞，见 §4 头部注 |
| 选项库 | 0 ✅ 已清空 | API 标 deprecated 休眠 |
| 实例/边 | 0 | K8s 链路已重构完成（附录 B #16），部署消费后即产出；云链路待第二批 |

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

---

## 3. 待建模型字段表

### 3.1 APISIX路由 `apisix_route`（K8S 分组，替代 k8s_ingress）

> 流量链路第三跳：dns_record → NLB → APISIX(k8s_service) → apisix_route → 后端 k8s_service。
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

### 3.3 不建模型的 Kind（纪律）

ReplicaSet、Endpoints、EndpointSlice、Event、Lease、Ingress（未使用，入口为 APISIX）；
ConfigMap/Secret 待"配置影响面分析"立项再议。

---

## 4. 模型关系约束（cmdb_model_relations 录入清单，45/52）

> relation_type 只有 belongs_to（从属/树）与 relates_to（关联/图）两种，业务语义写在关系名。
>
> 录入进度（2026-08-08）：46 条全部正确（#42 方向已修正为 `k8s_pvc→k8s_pv`）；
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

### aliyun_rds

engine(string) / engine_version(string) / instance_class(string) / storage_gb(number) /
connection_string(string) / port(number) / charge_type(enum) / expired_at(date) /
vswitch_id(string, 同步保留)。从属：aliyun_rds belongs_to aliyun_vswitch (1:n)。

### aliyun_redis

engine_version(string) / instance_class(string) / capacity_mb(number) /
connection_string(string) / port(number) / vswitch_id(string, 同步保留)。
从属：aliyun_redis belongs_to aliyun_vswitch (1:n)。

### gcp_cloudsql

engine(string) / engine_version(string) / tier(string) / storage_gb(number) /
private_ip(string) / public_ip(string) / vpc_id(string, 同步保留)。
从属：gcp_cloudsql belongs_to gcp_vpc (1:n)。

### gcp_disk（GKE 跑有状态服务、需 PV 桥接对端时建）

disk_type(string, pd-ssd/pd-balanced) / size_gb(number) / encrypted(boolean) /
users(json, 挂载实例列表, 同步保留)。
从属：gcp_disk belongs_to gcp_account (1:n)；
关联：gcp_disk relates_to gcp_compute (n:n, 挂载于)、k8s_pv relates_to gcp_disk (1:1, kind=csi)。

---

## 附录 B：同步侧待办备忘（与本清单联动）

| # | 事项 | 说明 |
|---|------|------|
| 1 | `cmdb_relates_to` 加 `kind` 列 | 唯一键改为 `UNIQUE(source_id, target_id, kind)`；EIP 直绑与 DNAT 暴露共存依赖它，做 NAT 同步前落地 |
| 2 | Kind → model_code 映射更新 | Deployment/StatefulSet/DaemonSet → k8s_workload（写 fields.workload_type）；不 Watch RS/Endpoints/EndpointSlice |
| 3 | Pod 属主两级解析 | ownerReferences Pod→RS→Deployment，直接建 pod belongs_to workload。**已确认 informer 不 Watch ReplicaSet**：消费端用 RS 名去掉末段 pod-template-hash 后缀得到 Deployment 名，无需 Go 侧改动 |
| 4 | Service↔Pod 双向触发 | Service 事件与 Pod 事件都要重算 selector 匹配边 |
| 5 | 孤儿认领 | 子资源先到时只落库不建边，父资源到位后按"同步保留"字段反向补边 |
| 6 | 网络资源降频采集 | VPC/子网/SG/防火墙 1h 级；主机 5–10min 级；DNS 30min–1h 全量对账 |
| 7 | GCP 防火墙级联删除 | 删 gcp_vpc 时软删对应 gcp_firewall 合成实例 |
| 8 | 规则/条目级 diff | rules_hash（SG/防火墙）、snat_hash/dnat_hash（NAT）不变则跳过 change_log，变更按 rule_id/entry_id 对齐输出 |
| 9 | ~~Informer 新增 Watch Kind~~ **已确认无需开发** | informer 已支持 13 种资源（含 persistentvolumes/persistentvolumeclaims），配置启用即可；endpoints 虽支持但纪律是不建模——配置里不启用；不支持 Ingress（与不建 Ingress 的决策一致） |
| 10 | APISIX 采集通道二选一 | CRD 模式：Informer 加 Watch ApisixRoute/ApisixUpstream，走现有 Topic；Admin API 模式：轻量拉取器 5–10min，只读 Key，凭据不进 git |
| 11 | 云采集器新增存储 API | DescribeDisks（含游离盘）/ DescribeFileSystems + 挂载点；降频策略同网络资源 1h 级 |
| 12 | builder 新增桥接/派生规则 | volume_handle→aliyun_disk/aliyun_nas；apisix_route.upstream_ref→k8s_service；route.host↔dns_record FQDN；**NAT DNAT 条目按 internal_ip 匹配 ECS 派生 `aliyun_ecs relates_to aliyun_eip (kind=dnat)` 边**（公网暴露面审计依赖） |
| 13 | 应用关联物化（后端表，非模型） | 新增 `cmdb_app_resources` 显式关联表；tag_key='app' 自动归集写入 source='tag'，手动绑定 source='manual'；应用只绑服务级 CI（workload/中间件/RDS/入口），不绑 Pod/Node |
| 14 | **K8s 消息契约以 cmdb-informer 为准重写** | 实测对比（`cmdb-informer/internal/message/types.go` 的 `MQMessage` vs `kafka_messages.py` 的 `K8sResourceMessage`）两边结构性不一致：字段名（cluster_id vs cluster、resource_type vs kind）、消息结构（Go 嵌套 `resource.{uid,name,labels,annotations,spec,status,raw}` vs Python 平铺）、Python 缺 `message_id`（幂等去重）/`sync_type`/`snapshot` 事件/`old_resource`。重构时照 Go 侧 `MQMessage` 重写 Python schema，Go 生产者不动；**已确认** `resource_type` 取值为小写复数（pods/services/deployments/statefulsets/daemonsets/nodes/namespaces/persistentvolumes/persistentvolumeclaims 等），消费端映射表按此格式编写 |
| 15 | 快照对账删除（依赖 #14） | informer 有 full_sync/periodic_sync 全量快照通道（event_type=snapshot）。消费端加快照会话逻辑：识别一轮全量的起止，结束后将本轮未出现的该集群资源做差集软删——补上仅靠 watch delete 事件丢事件即漏删的缺口 |
| 16 | 消费链路 v1→v2 重构（前置：模型/字段/约束录入完成）【K8s 链路 ✅ 已完成，云链路待第二批】 | 已落地：schema 照 Go `MQMessage` 重写；`k8s_extractors.py` 按 resource_type→model_code→model_id 提取 fields（deployments/statefulsets/daemonsets 归一 k8s_workload，configmaps/secrets 跳过）；labels 差异同步 source='cloud'；builder 按库内 12 条 K8s 约束建边（description=约束 relation_name），从属边整包替换、关联边按语义槽位替换；软删除同步清边；集群实例兜底创建（#17）；pod 边不记 change_log（#21）。跨云桥接边（#37/#38/#40/#41/#46/#47/#15/#16）留待云链路重构；#15 快照差集软删未实现（依赖消息序识别，后续迭代） |
| 17 | k8s_cluster 实例自动 upsert | 集群本身无 Watch 事件，但是建边根节点：消费端首次见到某 `cluster_id` 时自动创建 k8s_cluster 实例（cluster_type 落 fields），无需手录、无需 informer 发消息 |
| 18 | informer 侧零开发确认 | 建模所需 9 种 Kind（nodes/namespaces/pods/services/deployments/statefulsets/daemonsets/PV/PVC）informer 已全部支持，只需改 config.yaml 资源清单+namespace 白名单；唯一例外是 #10 选 CRD 模式时需加 Watch ApisixRoute/ApisixUpstream |
| 19 | 合成 ID / 单位换算规则汇总（consumer 实现时照抄） | `gcp_firewall.provider_id = fw:{project_id}:{vpc_name}`（每 VPC 一个合成实例）；`dns_record.provider_id`：阿里云用 RecordId，GCP 合成 `{zone}:{name}:{type}:{policy_key或value}`；`aliyun_oss.provider_id = Bucket 名`；`dns_record.name = FQDN`（@ 存裸域名，泛解析存 `*.example.com`）；`memory_gb = API 返回的 MB / 1024`（ECS/GCE，nano 规格存 0.5）；`k8s_cluster.provider` 标托管厂商（aliyun/gcp），是云与容器两个世界的桥 |
| 20 | 实例关系 API 单父校验 | 手工创建 belongs_to 边时 service 层校验：该 child 已有父则拒绝（或提示替换）；边表现状只有 UNIQUE(child_id, parent_id) 防重复边，不防一子多父 |
| 21 | 高 churn Kind（pod）边写入策略 | pod 边变更**不写 change_log**（discovery 数据审计价值≈0，防噪音淹没）；pod 删除靠边表外键 ON DELETE CASCADE 自动级联，无需主动删边；builder 对 pod 按事件增量 upsert，禁止全量重建（防死元组/VACUUM 压力） |
