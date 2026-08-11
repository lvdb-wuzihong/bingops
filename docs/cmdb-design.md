# CMDB 模块完整开发文档（v2）

> 本文档涵盖 CMDB 模块的技术架构、动态模型体系、资源实例管理、关系拓扑、标签体系和同步机制，作为开发阶段的唯一参考源。
>
> v2 变更：从固定资源模型升级为**动态模型驱动**架构，参考 eryajf CMDBench + BK Lite 设计理念，使用 PostgreSQL JSONB 替代 MongoDB。

---

## 1. 项目概述

### 1.1 定位

bingops CMDB 是一个**模型驱动**的多云资源管理平台，用户通过 UI 定义资源模型和字段，系统自动同步阿里云、AWS、GCP 云资源和 K8s 资源，提供资产视图、关系拓扑、标签管理和变更追溯能力。

### 1.2 核心理念

**先建模型，再录实例** — 不是代码写死资源类型，而是用户在 UI 上创建模型、定义字段、建立关系，然后通过自动发现或手动录入填充数据。

### 1.3 技术栈

| 组件 | 选型 | 说明 |
|------|------|------|
| 语言 | Python 3.13+ | 使用现代类型语法 |
| Web 框架 | FastAPI | 异步高性能 |
| ORM | SQLAlchemy 2.0+ | async 模式 |
| 数据库 | PostgreSQL 16+ | JSONB 存储动态字段 |
| 缓存 | Redis | 热关系缓存 |
| 消息队列 | Kafka | K8s 事件 + 云资源同步 |
| 数据校验 | Pydantic v2 | 请求/响应模型 |

### 1.4 分层架构

```
API Router（参数校验、依赖注入）
    ↓
Service（业务逻辑编排）
    ↓
Repository（数据库 CRUD 封装）
    ↓
SQLAlchemy Model → PostgreSQL
```

### 1.5 云资源管理范围

| 分类 | 阿里云 | AWS | GCP |
|------|--------|-----|-----|
| 计算 | ECS | EC2 | GCE |
| 容器 | ACK | EKS | GKE |
| 数据库 | RDS | RDS | Cloud SQL |
| 缓存 | Redis 云版 | ElastiCache | Memorystore |
| 网络 | VPC / VSwitch / SLB | VPC / Subnet / ALB | VPC / Subnet / Cloud LB |
| 安全 | SecurityGroup | Security Group | Firewall Rule |
| DNS | DNS 解析 | Route 53 | Cloud DNS |

不涉及自建数据中心资源（无物理机房、机柜、物理网络设备）。

---

## 2. 系统架构

### 2.1 整体架构图

```
┌─────────────────────────────────────────────────────────────┐
│                      数据生产层（独立应用）                    │
│                                                               │
│  ┌──────────────────┐           ┌──────────────────────┐    │
│  │ K8s Informer     │           │ 云资源定时采集器       │    │
│  │ Watch 事件        │           │ 调阿里云/AWS/GCP API  │    │
│  └────────┬─────────┘           └──────────┬───────────┘    │
│           ▼                                ▼                │
│  ┌─────────────────────────────────────────────────────┐   │
│  │                     Kafka                            │   │
│  │  Topic: k8s-events-{cluster_id}                      │   │
│  │  Topic: cloud-sync-{provider}                        │   │
│  └──────────────────────┬──────────────────────────────┘   │
└─────────────────────────┼───────────────────────────────────┘
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                  CMDB 服务（无状态，可多实例）                  │
│                                                               │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  模型管理层                                           │   │
│  │  ├── 模型分类（分类树）                                │   │
│  │  ├── 模型定义（模型 + 字段 + 分组）                    │   │
│  │  ├── 模型关系（模型间关系约束）                        │   │
│  │  └── 公共选项库（枚举值跨模型复用）                    │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                               │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  资源仓库层                                           │   │
│  │  ├── 实例管理（CRUD + 动态字段校验）                   │   │
│  │  ├── 关系拓扑（从属展开 + 关联查询）                   │   │
│  │  ├── 标签管理（定义 + 打标 + 查询）                    │   │
│  │  ├── 全文检索（跨模型搜索）                            │   │
│  │  └── 变更审计（变更历史 + 回溯）                       │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                               │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  Kafka Consumer（自动发现消费端）                      │   │
│  │  ├── 消费 k8s-events → 按模型映射写入实例 + 重建关系   │   │
│  │  └── 消费 cloud-sync → 按模型映射写入实例 + 重建关系   │   │
│  └──────────────────────────────────────────────────────┘   │
└───────────────────────────┬─────────────────────────────────┘
                            │
              ┌─────────────┼─────────────┐
              ▼             ▼             ▼
         PostgreSQL       Redis         Kafka
```

### 2.2 无状态设计要点

| 维度 | 实现方式 |
|------|----------|
| 状态外置 | 所有数据存 PostgreSQL + Redis，服务本身不持有状态 |
| 生产解耦 | Informer 和采集器在独立应用运行，CMDB 只消费 |
| 消费幂等 | Kafka 消息带 resource_version，重复消费安全 |
| 水平扩展 | 多 CMDB 实例组成 Kafka Consumer Group 并行消费 |

---

## 3. 动态模型体系（核心）

### 3.1 模型分类表 `cmdb_model_categories`

模型分类是模型的容器，形成左侧导航树。

```sql
CREATE TABLE cmdb_model_categories (
    id          BIGSERIAL PRIMARY KEY,
    name        VARCHAR(128) NOT NULL,          -- 显示名：计算资源、容器平台、网络...
    code        VARCHAR(64)  NOT NULL UNIQUE,    -- 编码：compute, container, network
    icon        VARCHAR(64),                     -- 图标标识
    sort_order  INT NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### 3.2 模型定义表 `cmdb_models`

每个模型代表一种资源类型（如 ECS 主机、VPC、K8s Pod）。

```sql
CREATE TABLE cmdb_models (
    id          BIGSERIAL PRIMARY KEY,
    category_id BIGINT NOT NULL REFERENCES cmdb_model_categories(id),
    name        VARCHAR(128) NOT NULL,          -- 显示名：ECS 主机、K8s Pod
    code        VARCHAR(64)  NOT NULL UNIQUE,    -- 编码：aliyun_ecs, k8s_pod
    icon        VARCHAR(64),                     -- 图标标识
    description TEXT,
    is_builtin  BOOLEAN NOT NULL DEFAULT FALSE,  -- 是否内置模型（不可删除）
    is_enabled  BOOLEAN NOT NULL DEFAULT TRUE,   -- 是否启用
    sort_order  INT NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_cmdb_model_category ON cmdb_models (category_id);
```

### 3.3 字段定义表 `cmdb_model_fields`

每个模型可自定义字段，字段值存储在实例的 `fields JSONB` 中。

```sql
CREATE TABLE cmdb_model_fields (
    id              BIGSERIAL PRIMARY KEY,
    model_id        BIGINT NOT NULL REFERENCES cmdb_models(id) ON DELETE CASCADE,
    name            VARCHAR(128) NOT NULL,       -- 显示名：CPU 核数、内网 IP
    code            VARCHAR(64)  NOT NULL,       -- 字段编码：cpu, private_ip
    field_type      VARCHAR(32)  NOT NULL,       -- 字段类型（见下表）
    group_name      VARCHAR(64),                 -- 字段分组：基础信息、网络配置、运维信息
    is_required     BOOLEAN NOT NULL DEFAULT FALSE,
    is_unique       BOOLEAN NOT NULL DEFAULT FALSE,
    is_searchable   BOOLEAN NOT NULL DEFAULT TRUE,
    is_builtin      BOOLEAN NOT NULL DEFAULT FALSE, -- 是否预置字段（不可删除）
    default_value   TEXT,
    placeholder     VARCHAR(256),
    options         JSONB,                       -- 下拉/枚举选项 [{"label":"运行中","value":"running"},...]
    option_set_id   BIGINT,                      -- 引用公共选项库（可选）
    sort_order      INT NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (model_id, code)
);

CREATE INDEX idx_cmdb_field_model ON cmdb_model_fields (model_id);
```

#### 支持的字段类型

| field_type | 说明 | JSONB 存储格式 | options 示例 |
|------------|------|---------------|-------------|
| `string` | 文本 | `"hello"` | — |
| `number` | 数字 | `42` | — |
| `boolean` | 布尔 | `true` | — |
| `date` | 日期 | `"2026-01-01"` | — |
| `datetime` | 日期时间 | `"2026-01-01T12:00:00Z"` | — |
| `enum` | 单选下拉 | `"running"` | `[{"label":"运行中","value":"running"}]` |
| `multi_enum` | 多选 | `["tag1","tag2"]` | `[{"label":"标签1","value":"tag1"}]` |
| `password` | 密码（加密存储） | 密文 | — |
| `json` | 自由 JSON | `{"key":"value"}` | — |

### 3.4 模型关系定义表 `cmdb_model_relations`

定义模型之间允许建立的关系约束，前端据此引导用户创建实例关系。

```sql
CREATE TABLE cmdb_model_relations (
    id                BIGSERIAL PRIMARY KEY,
    source_model_id   BIGINT NOT NULL REFERENCES cmdb_models(id) ON DELETE CASCADE,
    target_model_id   BIGINT NOT NULL REFERENCES cmdb_models(id) ON DELETE CASCADE,
    relation_type     VARCHAR(16) NOT NULL,      -- 'belongs_to' | 'relates_to'
    relation_name     VARCHAR(128),              -- 关系显示名：运行于、依赖于、关联
    source_mapping    VARCHAR(8) NOT NULL DEFAULT '1:n', -- '1:1' | '1:n' | 'n:n'
    description       TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source_model_id, target_model_id, relation_type)
);
```

#### 关系类型说明

| 关系类型 | 本质 | 方向 | 典型场景 |
|----------|------|------|---------|
| `belongs_to` | 从属（树形） | child → parent | 子网→VPC，Pod→Node，ECS→子网 |
| `relates_to` | 关联（图） | source ↔ target | SLB↔ECS，Service↔Pod，App↔RDS |

### 3.5 公共选项库 `cmdb_option_sets`

跨模型复用的枚举值集合，如「状态」「环境类型」等。

```sql
CREATE TABLE cmdb_option_sets (
    id          BIGSERIAL PRIMARY KEY,
    name        VARCHAR(128) NOT NULL,           -- 显示名：资源状态、环境类型
    code        VARCHAR(64)  NOT NULL UNIQUE,    -- 编码：resource_status, env_type
    options     JSONB NOT NULL,                  -- [{"label":"运行中","value":"running","color":"green"},...]
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

**预置选项集：**

| code | name | options |
|------|------|---------|
| `resource_status` | 资源状态 | 运行中(running)、已停止(stopped)、维护中(maintenance)、未知(unknown) |
| `env_type` | 环境类型 | 生产(production)、预发(staging)、测试(test)、开发(dev) |
| `cloud_provider` | 云厂商 | 阿里云(aliyun)、AWS(aws)、GCP(gcp)、K8s(k8s) |

### 3.6 预置模型分类与模型

系统初始化时预置以下模型，用户可通过 UI 增删改。

| 分类 | 分类 code | 模型 | 模型 code |
|------|-----------|------|-----------|
| 计算资源 | compute | ECS 主机 / EC2 / GCE | aliyun_ecs, aws_ec2, gcp_gce |
| 容器平台 | container | K8s 集群 / Namespace / Deployment / Pod / Service / Node | k8s_cluster, k8s_namespace, k8s_deployment, k8s_pod, k8s_service, k8s_node |
| 数据库 | database | RDS / Cloud SQL | aliyun_rds, aws_rds, gcp_cloudsql |
| 缓存 | cache | Redis / ElastiCache / Memorystore | aliyun_redis, aws_elasticache, gcp_memorystore |
| 网络 | network | VPC / 子网 / 安全组 / 负载均衡 | vpc, subnet, security_group, load_balancer |
| DNS | dns | DNS 域 / DNS 记录 | dns_zone, dns_record |
| 云账号 | cloud_account | 云账号 | cloud_account |

---

## 4. 资源实例

### 4.1 资源实例表 `cmdb_resources`

所有模型共用一张表，动态字段存 JSONB。

```sql
CREATE TABLE cmdb_resources (
    id                BIGSERIAL PRIMARY KEY,

    -- 模型关联
    model_id          BIGINT NOT NULL REFERENCES cmdb_models(id),

    -- 通用字段（所有模型共有，可建索引）
    name              VARCHAR(256) NOT NULL,       -- 实例名称
    provider          VARCHAR(32),                 -- 'aliyun' | 'aws' | 'gcp' | 'k8s' | 'manual'
    provider_id       VARCHAR(256),                -- 云厂商原始 ID（幂等键）
    cloud_account     VARCHAR(128),                -- 云账号标识
    region            VARCHAR(64),                 -- 地域
    zone              VARCHAR(64),                 -- 可用区
    status            VARCHAR(32) NOT NULL DEFAULT 'unknown',

    -- 动态字段（按模型定义填充）
    fields            JSONB NOT NULL DEFAULT '{}',

    -- 同步元数据
    resource_version  VARCHAR(64),                 -- K8s ResourceVersion 或云 API 版本号
    synced_at         TIMESTAMPTZ,
    source            VARCHAR(32) NOT NULL DEFAULT 'manual', -- 'manual' | 'discovery' | 'kafka'

    -- 软删除
    deleted_at        TIMESTAMPTZ,

    -- 审计
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- 唯一约束
    UNIQUE (model_id, provider, provider_id, cloud_account)
);

-- 通用字段索引（高频查询）
CREATE INDEX idx_cmdb_resource_model     ON cmdb_resources (model_id);
CREATE INDEX idx_cmdb_resource_provider  ON cmdb_resources (provider);
CREATE INDEX idx_cmdb_resource_status    ON cmdb_resources (status);
CREATE INDEX idx_cmdb_resource_name      ON cmdb_resources (name);
CREATE INDEX idx_cmdb_resource_region    ON cmdb_resources (region);
CREATE INDEX idx_cmdb_resource_account   ON cmdb_resources (cloud_account);
CREATE INDEX idx_cmdb_resource_synced    ON cmdb_resources (synced_at);

-- 动态字段 GIN 索引（支持 JSONB 路径查询）
CREATE INDEX idx_cmdb_resource_fields    ON cmdb_resources USING GIN (fields);
```

### 4.2 通用字段 vs 动态字段分层

| 层 | 存储位置 | 字段 | 查询方式 |
|----|---------|------|---------|
| **通用层** | 表列 | name, provider, region, zone, status, cloud_account | B-tree 索引，直接 WHERE |
| **扩展层** | `fields` JSONB | cpu, memory, os, ip, engine, port... | GIN 索引 + JSONB 路径查询 |

**设计原则：** 所有资源都有的、高频筛选的字段放通用层；模型特有的字段放扩展层。

### 4.3 预置模型字段示例

#### ECS 主机 (aliyun_ecs)

| 字段 | code | field_type | 分组 | 必填 |
|------|------|-----------|------|------|
| CPU 核数 | cpu | number | 基础信息 | 是 |
| 内存(MB) | memory_mb | number | 基础信息 | 是 |
| 操作系统 | os | string | 基础信息 | 是 |
| 实例规格 | instance_class | string | 基础信息 | 是 |
| 内网 IP | private_ip | string | 网络配置 | 是 |
| 公网 IP | public_ip | string | 网络配置 | 否 |
| 磁盘(GB) | disk_size_gb | number | 存储配置 | 否 |
| 密钥对 | key_pair | string | 运维信息 | 否 |

fields JSONB 示例：
```json
{"cpu": 4, "memory_mb": 16384, "os": "ubuntu-22.04", "instance_class": "ecs.g7.xlarge",
 "private_ip": "10.0.1.10", "public_ip": "47.100.x.x", "disk_size_gb": 100}
```

#### K8s Pod (k8s_pod)

| 字段 | code | field_type | 分组 | 必填 |
|------|------|-----------|------|------|
| 运行状态 | phase | enum | 基础信息 | 是 |
| 节点 | node_name | string | 调度信息 | 是 |
| 容器列表 | containers | json | 基础信息 | 否 |
| 重启次数 | restart_count | number | 运行状态 | 否 |
| Pod IP | pod_ip | string | 网络信息 | 否 |
| QoS 级别 | qos_class | enum | 运行状态 | 否 |

#### VPC (vpc)

| 字段 | code | field_type | 分组 | 必填 |
|------|------|-----------|------|------|
| CIDR 块 | cidr_block | string | 网络配置 | 是 |
| 描述 | description | string | 基础信息 | 否 |
| 资源组 | resource_group | string | 基础信息 | 否 |

---

## 5. 关系模型

### 5.1 关系分类

| 类型 | 用途 | 结构 | 回答的问题 |
|------|------|------|------------|
| **从属** (belongs_to) | 层级归属 | 树形，child→parent | "它在哪 / 属于谁" |
| **关联** (relates_to) | 对等关联 | 图，source↔target | "它跟谁有关" |

### 5.2 从属关系表 `cmdb_belongs_to`

```sql
CREATE TABLE cmdb_belongs_to (
    id          BIGSERIAL PRIMARY KEY,
    child_id    BIGINT NOT NULL REFERENCES cmdb_resources(id) ON DELETE CASCADE,
    parent_id   BIGINT NOT NULL REFERENCES cmdb_resources(id) ON DELETE CASCADE,
    description VARCHAR(256),                    -- 关系描述：运行于、部署在

    synced_at   TIMESTAMPTZ,
    source      VARCHAR(32) NOT NULL DEFAULT 'discovery',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (child_id, parent_id)
);

CREATE INDEX idx_cmdb_belongs_child  ON cmdb_belongs_to (child_id);
CREATE INDEX idx_cmdb_belongs_parent ON cmdb_belongs_to (parent_id);
```

### 5.3 关联关系表 `cmdb_relates_to`

```sql
CREATE TABLE cmdb_relates_to (
    id          BIGSERIAL PRIMARY KEY,
    source_id   BIGINT NOT NULL REFERENCES cmdb_resources(id) ON DELETE CASCADE,
    target_id   BIGINT NOT NULL REFERENCES cmdb_resources(id) ON DELETE CASCADE,
    description VARCHAR(256),                    -- 关系描述：负载均衡后端、依赖数据库

    attributes  JSONB NOT NULL DEFAULT '{}',     -- 关系附加属性
    synced_at   TIMESTAMPTZ,
    source      VARCHAR(32) NOT NULL DEFAULT 'discovery',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (source_id, target_id)
);

CREATE INDEX idx_cmdb_relates_source ON cmdb_relates_to (source_id);
CREATE INDEX idx_cmdb_relates_target ON cmdb_relates_to (target_id);
```

### 5.4 典型关系示例

| child/source | 关系类型 | parent/target | 描述 |
|-------------|---------|--------------|------|
| ECS 主机 | belongs_to | 子网 | 网络归属 |
| 子网 | belongs_to | VPC | 网络归属 |
| K8s Pod | belongs_to | K8s Node | 调度位置 |
| K8s Namespace | belongs_to | K8s 集群 | 集群归属 |
| K8s Deployment | belongs_to | K8s Namespace | 命名空间归属 |
| RDS 实例 | belongs_to | VPC | 网络归属 |
| K8s Service | relates_to | K8s Pod | selector 匹配 |
| SLB | relates_to | ECS 主机 | 负载均衡后端 |
| K8s Ingress | relates_to | K8s Service | 路由转发 |
| DNS 记录 | relates_to | SLB | DNS 解析目标 |

### 5.5 从属关系树

```
CloudAccount
  └── Region (通用字段)
        └── VPC
              ├── Subnet
              │     ├── ECS / EC2 / GCE
              │     │     └── K8s Node
              │     ├── K8s Cluster
              │     │     └── K8s Namespace
              │     │           ├── K8s Deployment
              │     │           ├── K8s StatefulSet
              │     │           ├── K8s Service
              │     │           ├── K8s Ingress
              │     │           └── K8s Pod
              │     ├── RDS / Cloud SQL
              │     ├── Redis / ElastiCache
              │     └── SLB / ALB / Cloud LB
              └── SecurityGroup

DNSZone
  └── DNSRecord
```

---

## 6. 标签体系

### 6.1 设计原则

- 标签是独立模块，不是资源的附属字段
- 三种来源：云同步 (cloud)、手动打标 (manual)、规则引擎 (rule)
- 同一 tag_key 冲突时，手动标签优先
- 云标签 key 归一化（小写），raw_key 保留原始值

### 6.2 标签定义表 `cmdb_tag_definitions`

```sql
CREATE TABLE cmdb_tag_definitions (
    id              BIGSERIAL PRIMARY KEY,
    tag_key         VARCHAR(128) NOT NULL UNIQUE,
    name            VARCHAR(256) NOT NULL,
    description     TEXT,
    category        VARCHAR(32) NOT NULL DEFAULT 'custom',
    value_type      VARCHAR(16) NOT NULL DEFAULT 'string',
    allowed_values  JSONB,
    editable        BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

预置系统标签：env（环境）、app（归属应用）、team（所属团队）、owner（负责人）。

### 6.3 资源标签关联表 `cmdb_resource_tags`

```sql
CREATE TABLE cmdb_resource_tags (
    id          BIGSERIAL PRIMARY KEY,
    resource_id BIGINT NOT NULL REFERENCES cmdb_resources(id) ON DELETE CASCADE,
    tag_key     VARCHAR(128) NOT NULL,
    tag_value   TEXT NOT NULL,
    source      VARCHAR(16) NOT NULL DEFAULT 'manual',
    raw_key     VARCHAR(256),
    synced_at   TIMESTAMPTZ,
    operator    VARCHAR(128),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (resource_id, tag_key, source)
);

CREATE INDEX idx_cmdb_tag_resource  ON cmdb_resource_tags (resource_id);
CREATE INDEX idx_cmdb_tag_key       ON cmdb_resource_tags (tag_key);
CREATE INDEX idx_cmdb_tag_value     ON cmdb_resource_tags (tag_value);
CREATE INDEX idx_cmdb_tag_key_value ON cmdb_resource_tags (tag_key, tag_value);
CREATE INDEX idx_cmdb_tag_source    ON cmdb_resource_tags (source);
```

### 6.4 云标签归一化

- 阿里云：`[{"Key":"env","Value":"prod"}]` → `{"env":"prod"}`
- AWS：`[{"Key":"env","Value":"prod"}]` → `{"env":"prod"}`
- GCP：`{"env":"production"}` → 直接小写使用
- 统一：key 转小写 + 短横线，raw_key 保留原始值

### 6.5 同步冲突处理

```
1. 查出该资源 source='manual' 的 tag_key 集合
2. 云标签 key 在手动集合中 → 跳过（手动优先）
3. 不在 → upsert (source='cloud')
4. 删除 source='cloud' 中云上已不存在的旧标签
5. source='rule' 不受影响
```

---

## 7. 业务应用

### 7.1 业务应用表 `cmdb_business_apps`

```sql
CREATE TABLE cmdb_business_apps (
    id          BIGSERIAL PRIMARY KEY,
    app_code    VARCHAR(64) NOT NULL UNIQUE,
    name        VARCHAR(256) NOT NULL,
    description TEXT,
    team        VARCHAR(128),
    owner       VARCHAR(128),
    department  VARCHAR(128),
    labels      JSONB NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### 7.2 关联方式

通过 `cmdb_resource_tags` 中 `tag_key='app'` 关联，不使用外键。

---

## 8. 变更记录

### 8.1 变更记录表 `cmdb_change_logs`

```sql
CREATE TABLE cmdb_change_logs (
    id          BIGSERIAL PRIMARY KEY,
    resource_id BIGINT NOT NULL,
    model_id    BIGINT,                          -- 冗余模型 ID
    change_type VARCHAR(16) NOT NULL,
    field       VARCHAR(128),
    old_value   TEXT,
    new_value   TEXT,
    source      VARCHAR(32) NOT NULL DEFAULT 'manual',
    operator    VARCHAR(128),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_cmdb_change_resource ON cmdb_change_logs (resource_id);
CREATE INDEX idx_cmdb_change_model    ON cmdb_change_logs (model_id);
CREATE INDEX idx_cmdb_change_type     ON cmdb_change_logs (change_type);
CREATE INDEX idx_cmdb_change_time     ON cmdb_change_logs (created_at);
```

---

## 9. 同步机制

### 9.1 K8s 资源同步（事件驱动）

```
K8s API Server → Informer Watch → Kafka Producer → k8s-events-{cluster_id}
                                                         ↓
                                              CMDB Kafka Consumer
                                              ├── 按 Kind 映射到 model_code（如 Pod → k8s_pod）
                                              ├── 幂等 upsert（resource_version 校验）
                                              ├── 同步 labels → cmdb_resource_tag
                                              └── 重建从属 + 关联关系
```

**K8s Kind → 模型映射：**

| Kind | model_code |
|------|-----------|
| Pod | k8s_pod |
| Deployment | k8s_deployment |
| StatefulSet | k8s_statefulset |
| DaemonSet | k8s_daemonset |
| ReplicaSet | k8s_replicaset |
| Service | k8s_service |
| Ingress | k8s_ingress |
| ConfigMap | k8s_configmap |
| Secret | k8s_secret |
| PersistentVolumeClaim | k8s_pvc |
| Node | k8s_node |
| Namespace | k8s_namespace |

### 9.2 云资源同步（定时拉取）

> 实现设计以 `docs/cloud-sync-design.md` 为准（独立项目 cloud-syncer）；本节仅为机制概览，下方映射表为早期草稿，模型 code 以 `cmdb-model-presets.md` 为准。

```
定时调度器 → 阿里云/AWS/GCP SDK → 全量资源列表 → 差异对比
                                                        ↓
                                          Kafka Producer → cloud-sync-{provider}
                                                        ↓
                                              CMDB Kafka Consumer
                                              ├── 按 resource_type 映射到 model_code
                                              ├── 幂等 upsert
                                              ├── 同步云标签
                                              └── 重建关系
```

**云资源 → 模型映射：**

| 云资源 | model_code | provider |
|--------|-----------|----------|
| ECS / EC2 / GCE | aliyun_ecs / aws_ec2 / gcp_gce | aliyun / aws / gcp |
| RDS / Cloud SQL | aliyun_rds / aws_rds / gcp_cloudsql | — |
| Redis / ElastiCache / Memorystore | aliyun_redis / aws_elasticache / gcp_memorystore | — |
| VPC | vpc | — |
| Subnet / VSwitch | subnet | — |
| SecurityGroup | security_group | — |
| SLB / ALB / Cloud LB | load_balancer | — |
| DNS Zone / Record | dns_zone / dns_record | — |

### 9.3 Kafka 消息格式

**K8s 事件：**
```json
{
  "cluster": "ack-cn-shanghai",
  "kind": "Pod",
  "namespace": "production",
  "name": "order-svc-7d4f6bf4b-x2k9p",
  "event_type": "update",
  "resource_version": "123456",
  "data": { ... },
  "labels": {"app": "order-service"},
  "timestamp": "2026-07-10T10:00:00Z"
}
```

**云资源事件：**
```json
{
  "provider": "aliyun",
  "resource_type": "host",
  "provider_id": "i-bp1xxxxxx",
  "cloud_account": "1234567890",
  "event_type": "upsert",
  "resource_version": "1",
  "name": "prod-web-01",
  "region": "cn-shanghai",
  "zone": "cn-shanghai-a",
  "status": "running",
  "fields": {"cpu": 4, "memory_mb": 16384, "os": "ubuntu-22.04", "private_ip": "10.0.1.10"},
  "cloud_tags": {"env": "production"},
  "parent_provider_id": "vsw-xxx",
  "parent_resource_type": "subnet",
  "timestamp": "2026-07-10T10:00:00Z"
}
```

### 9.4 关系重建策略

消费事件后，根据资源元数据主动重建关系：

```
收到 K8s Pod 事件
  → Upsert 实例（model_id=k8s_pod）
  → 重建从属：Pod → ReplicaSet → Deployment → Namespace → Cluster
  → 重建从属：Pod → Node（通过 spec.nodeName）

收到 K8s Service 事件
  → Upsert 实例
  → 根据 spec.selector 查询匹配的 Pod
  → 重建关联：Service ↔ Pod

收到云 Host 同步
  → Upsert 实例
  → 重建从属：Host → Subnet → VPC（通过 attributes 中的 vpc_id/subnet_id）
  → 重建关联：Host ↔ K8s Node（通过 IP 匹配）
```

---

## 10. API 设计

### 10.1 模型管理

```
GET    /api/v1/cmdb/models/categories                     # 分类列表
POST   /api/v1/cmdb/models/categories                     # 创建分类
PUT    /api/v1/cmdb/models/categories/{id}                # 更新分类
DELETE /api/v1/cmdb/models/categories/{id}                # 删除分类

GET    /api/v1/cmdb/models                                # 模型列表（可按分类筛选）
POST   /api/v1/cmdb/models                                # 创建模型
GET    /api/v1/cmdb/models/{id}                           # 模型详情（含字段+关系定义）
PUT    /api/v1/cmdb/models/{id}                           # 更新模型
DELETE /api/v1/cmdb/models/{id}                           # 删除模型（仅非内置）

GET    /api/v1/cmdb/models/{id}/fields                    # 字段列表
POST   /api/v1/cmdb/models/{id}/fields                    # 添加字段
PUT    /api/v1/cmdb/models/{id}/fields/{field_id}         # 更新字段
DELETE /api/v1/cmdb/models/{id}/fields/{field_id}         # 删除字段

GET    /api/v1/cmdb/models/{id}/relations                 # 模型关系列表
POST   /api/v1/cmdb/models/{id}/relations                 # 添加模型关系
DELETE /api/v1/cmdb/models/{id}/relations/{rel_id}         # 删除模型关系

GET    /api/v1/cmdb/models/option-sets                    # 公共选项列表
POST   /api/v1/cmdb/models/option-sets                    # 创建选项集
PUT    /api/v1/cmdb/models/option-sets/{id}               # 更新选项集
DELETE /api/v1/cmdb/models/option-sets/{id}               # 删除选项集
```

### 10.2 资源实例

```
GET    /api/v1/cmdb/resources                           # 实例列表（分页+筛选）
GET    /api/v1/cmdb/resources/stats                     # 统计（按模型、状态、云厂商）
POST   /api/v1/cmdb/resources                           # 创建实例
GET    /api/v1/cmdb/resources/{id}                      # 实例详情
PUT    /api/v1/cmdb/resources/{id}                      # 更新实例
DELETE /api/v1/cmdb/resources/{id}                      # 软删除
POST   /api/v1/cmdb/resources/import                    # 批量导入（Excel）
GET    /api/v1/cmdb/resources/export                    # 批量导出
GET    /api/v1/cmdb/resources/search?q=keyword          # 全文检索
```

**列表查询参数：**

| 参数 | 类型 | 说明 |
|------|------|------|
| `model_id` | int | 按模型过滤 |
| `provider` | string | 按云厂商过滤 |
| `status` | string | 按状态过滤 |
| `region` | string | 按地域过滤 |
| `cloud_account` | string | 按云账号过滤 |
| `keyword` | string | 名称/provider_id 模糊搜索 |
| `tag_key` + `tag_value` | string | 标签过滤 |
| `fields.*` | any | 动态字段查询（如 `fields.cpu=4`） |

### 10.3 关系拓扑

```
GET    /api/v1/cmdb/resources/{id}/belongs-to           # 向上查从属链
GET    /api/v1/cmdb/resources/{id}/children              # 向下查从属链
GET    /api/v1/cmdb/resources/{id}/relations             # 查关联关系
GET    /api/v1/cmdb/resources/{id}/topology              # 完整拓扑
POST   /api/v1/cmdb/resources/belongs-to                 # 创建从属关系
DELETE /api/v1/cmdb/resources/belongs-to/{id}            # 删除从属关系
POST   /api/v1/cmdb/resources/relates-to                 # 创建关联关系
DELETE /api/v1/cmdb/resources/relates-to/{id}            # 删除关联关系
```

### 10.4 标签管理

```
GET    /api/v1/cmdb/tags/definitions                     # 标签定义列表
POST   /api/v1/cmdb/tags/definitions                     # 创建标签定义
PUT    /api/v1/cmdb/tags/definitions/{id}                # 更新
DELETE /api/v1/cmdb/tags/definitions/{id}                # 删除

GET    /api/v1/cmdb/resources/{id}/tags                  # 查资源标签
POST   /api/v1/cmdb/resources/{id}/tags                  # 打标
DELETE /api/v1/cmdb/resources/{id}/tags/{tag_key}         # 删标签
POST   /api/v1/cmdb/tags/batch                           # 批量打标
```

### 10.5 业务应用 + 变更审计

```
GET/POST        /api/v1/cmdb/apps                        # 应用 CRUD
GET/PUT/DELETE  /api/v1/cmdb/apps/{id}
GET             /api/v1/cmdb/apps/{id}/resources          # 应用关联资源

GET             /api/v1/cmdb/resources/{id}/changes       # 资源变更历史
GET             /api/v1/cmdb/changes                      # 全局变更日志
```

### 10.6 响应格式

统一信封：
```json
{"code": 0, "message": "success", "data": {...}, "request_id": "req-abc123"}
```

---

## 11. 实施路线

### Phase 1：模型管理（基础）

- 模型分类 CRUD
- 模型 CRUD
- 字段定义 CRUD（含字段分组）
- 公共选项库 CRUD
- 模型关系定义 CRUD
- 预置模型初始化（云资源 + K8s 模型）

### Phase 2：资源仓库（核心）

- 资源实例 CRUD（动态字段校验）
- 实例列表（按模型导航 + 动态列渲染）
- 全文检索（跨模型搜索）
- 批量导入/导出
- 资源统计面板

### Phase 3：关系与拓扑

- 实例关系管理（从属 + 关联）
- 拓扑视图（树形展开 + 图遍历）
- 模型关系约束校验

### Phase 4：标签与变更

- 标签定义 + 打标 + 云标签同步
- 标签规则引擎
- 业务应用管理
- 变更审计日志

### Phase 5：自动发现（Kafka 同步）

- Kafka 消费者（K8s 事件 + 云资源）
- 模型映射引擎（Kind/resource_type → model_code）
- 关系重建引擎
- 云标签归一化同步

### Phase 6：高级能力

- 数据订阅（属性变更/关联变更通知）
- 模型复制（快速复制标准模型）
- 高级搜索（组合条件保存为查询模板）

---

## 12. 目录结构

```
bingops/
├── api/v1/cmdb/
│   ├── models.py              # 模型分类 + 模型 + 字段 + 关系定义 + 选项库 API
│   ├── resources.py           # 资源实例 API
│   ├── relationships.py       # 关系拓扑 API
│   ├── tags.py                # 标签管理 API
│   ├── apps.py                # 业务应用 API
│   └── changes.py             # 变更审计 API
├── models/cmdb/
│   ├── model.py               # ModelCategory / Model / ModelField / ModelRelation / OptionSet ORM
│   ├── resource.py            # Resource ORM
│   ├── relationship.py        # BelongsTo / RelatesTo ORM
│   ├── tag.py                 # TagDefinition / ResourceTag ORM
│   ├── business_app.py        # BusinessApp ORM
│   └── change_log.py          # ChangeLog ORM
├── schemas/cmdb/
│   ├── model.py               # 模型相关 Pydantic 模型
│   ├── resource.py            # 实例相关 Pydantic 模型
│   ├── relationship.py
│   ├── tag.py
│   ├── business_app.py
│   ├── change_log.py
│   └── kafka_messages.py
├── services/cmdb/
│   ├── model_service.py       # 模型管理业务逻辑
│   ├── resource_service.py    # 实例管理（含动态字段校验）
│   ├── relationship_service.py
│   ├── tag_service.py
│   ├── business_app_service.py
│   └── change_log_service.py
├── repositories/cmdb/
│   ├── model_repo.py
│   ├── resource_repo.py
│   ├── relationship_repo.py
│   ├── tag_repo.py
│   ├── business_app_repo.py
│   └── change_log_repo.py
├── tasks/cmdb/
│   ├── startup.py             # Kafka 消费者启动器
│   ├── k8s_consumer.py        # K8s 事件消费
│   ├── cloud_consumer.py      # 云资源消费
│   └── relationship_builder.py # 关系重建引擎
└── kafka/
    └── client.py              # Kafka 客户端封装
```

---

## 13. 关键设计决策

| # | 决策 | 选择 | 理由 |
|---|------|------|------|
| 1 | 数据模型 | 动态模型（用户定义） | 运维环境差异大，固定模型无法适应 |
| 2 | 数据库 | PostgreSQL + JSONB | 替代 MongoDB，统一技术栈，JSONB 支持动态字段 |
| 3 | 字段分层 | 通用层（表列）+ 扩展层（JSONB） | 高频字段走 B-tree 索引，低频走 GIN |
| 4 | 关系分类 | 从属 + 关联（两种） | 从属=树形层级，关联=灵活图，覆盖所有场景 |
| 5 | 模型关系约束 | 模型层定义 + 实例层校验 | 前端据此引导用户，减少错误关系 |
| 6 | 标签存储 | 独立标签表 | 支持多源隔离、审计、权限控制 |
| 7 | K8s 同步 | Informer + Kafka | 实时性好，天然全量+增量 |
| 8 | 云资源同步 | 定时拉取 → Kafka → 消费 | 云 API 统一，Pull 模式可靠 |
| 9 | 标签冲突 | 手动优先 | 人工判断高于自动同步 |
| 10 | 公共选项库 | 独立表 + 模型字段引用 | 状态/环境等枚举值跨模型复用 |
| 11 | 主键策略 | BIGINT 自增 | 性能好，序列化友好 |
