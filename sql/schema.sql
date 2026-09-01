-- ============================================================================
-- BingOps 运维平台数据库结构（全量基线）
-- 适用数据库：PostgreSQL 14+
-- 执行方式：psql -U <user> -d <dbname> -f sql/schema.sql
--
-- 基线状态：已整合 v2（CMDB 动态模型）、v3（同步任务）、v4（工单）、
--          v7（表驱动同步 / 结构对齐）迁移的最终结构，与 ORM 模型一致。
-- 存量库升级请走 sql/migrations/ 下的增量脚本，不要重跑本文件。
--
-- 说明：
-- - 旧版 v1 草稿表（hosts/host_groups/credentials/playbooks/deployments/
--   deployment_results/tasks/audit_logs）后端从未实现，已从基线移除；
--   相关权限码保留（前端动态菜单引用），待对应模块 v2 重新设计时复用。
-- - 发布/任务/工单等新模块表请按现行规范（BIGINT 主键 + JSONB + BaseMixin）
--   独立设计，勿复用已移除的 v1 草稿。
-- ============================================================================

-- ============================================================================
-- 认证与权限（Auth & RBAC）
-- ============================================================================

-- 用户表
CREATE TABLE users (
    id              BIGSERIAL PRIMARY KEY,
    username        VARCHAR(64)  NOT NULL UNIQUE,
    email           VARCHAR(255) NOT NULL UNIQUE,
    password_hash   VARCHAR(255),
    display_name    VARCHAR(128),
    auth_source     VARCHAR(16)  NOT NULL DEFAULT 'local',  -- 'local' | 'feishu'
    feishu_open_id  VARCHAR(128) UNIQUE,
    feishu_union_id VARCHAR(128),
    avatar_url      VARCHAR(512),
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    is_superuser    BOOLEAN      NOT NULL DEFAULT FALSE,
    last_login_at   TIMESTAMPTZ,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_users_username ON users (username);
CREATE INDEX idx_users_email    ON users (email);

-- 角色表
CREATE TABLE roles (
    id          BIGSERIAL PRIMARY KEY,
    code        VARCHAR(64)  NOT NULL UNIQUE,
    name        VARCHAR(128) NOT NULL,
    description TEXT,
    is_system   BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- 权限表
CREATE TABLE permissions (
    id          BIGSERIAL PRIMARY KEY,
    code        VARCHAR(128) NOT NULL UNIQUE,
    name        VARCHAR(128) NOT NULL,
    description TEXT,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- 角色-权限关联表
CREATE TABLE role_permissions (
    role_id       BIGINT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    permission_id BIGINT NOT NULL REFERENCES permissions(id) ON DELETE CASCADE,
    PRIMARY KEY (role_id, permission_id)
);

-- 用户-角色关联表
CREATE TABLE user_roles (
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role_id BIGINT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, role_id)
);

-- ============================================================================
-- CMDB 配置管理数据库（v2 动态模型）
-- ============================================================================

-- 模型分类表
CREATE TABLE cmdb_model_categories (
    id          BIGSERIAL PRIMARY KEY,
    name        VARCHAR(128) NOT NULL,               -- 显示名：计算资源、容器平台、网络...
    code        VARCHAR(64)  NOT NULL UNIQUE,         -- 编码：compute, container, network
    icon        VARCHAR(64),                          -- 图标标识
    sort_order  INT          NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- 模型定义表
CREATE TABLE cmdb_models (
    id          BIGSERIAL PRIMARY KEY,
    category_id BIGINT       NOT NULL REFERENCES cmdb_model_categories(id),
    name        VARCHAR(128) NOT NULL,               -- 显示名：ECS 主机、K8s Pod
    code        VARCHAR(64)  NOT NULL UNIQUE,         -- 编码：aliyun_ecs, k8s_pod
    icon        VARCHAR(64),                          -- 图标标识
    description TEXT,
    is_builtin  BOOLEAN      NOT NULL DEFAULT FALSE,  -- 是否内置模型（不可删除）
    is_enabled  BOOLEAN      NOT NULL DEFAULT TRUE,   -- 是否启用
    sort_order  INT          NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_cmdb_model_category ON cmdb_models (category_id);

-- 字段定义表
CREATE TABLE cmdb_model_fields (
    id              BIGSERIAL PRIMARY KEY,
    model_id        BIGINT       NOT NULL REFERENCES cmdb_models(id) ON DELETE CASCADE,
    name            VARCHAR(128) NOT NULL,             -- 显示名：CPU 核数、内网 IP
    code            VARCHAR(64)  NOT NULL,             -- 字段编码：cpu, private_ip
    field_type      VARCHAR(32)  NOT NULL,             -- string|number|boolean|date|datetime|enum|multi_enum|password|json
    group_name      VARCHAR(64),                      -- 字段分组：基础信息、网络配置、运维信息
    is_required     BOOLEAN      NOT NULL DEFAULT FALSE,
    is_unique       BOOLEAN      NOT NULL DEFAULT FALSE,
    is_searchable   BOOLEAN      NOT NULL DEFAULT TRUE,
    is_builtin      BOOLEAN      NOT NULL DEFAULT FALSE, -- 是否预置字段（不可删除）
    default_value   TEXT,
    placeholder     VARCHAR(256),
    options         JSONB,                            -- [{"label":"运行中","value":"running"},...]
    option_set_id   BIGINT,                           -- 引用公共选项库
    sort_order      INT          NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (model_id, code)
);

CREATE INDEX idx_cmdb_field_model ON cmdb_model_fields (model_id);

-- 模型关系定义表（关系简化方案：仅 belongs_to / relates_to 两类）
CREATE TABLE cmdb_model_relations (
    id                BIGSERIAL PRIMARY KEY,
    source_model_id   BIGINT       NOT NULL REFERENCES cmdb_models(id) ON DELETE CASCADE,
    target_model_id   BIGINT       NOT NULL REFERENCES cmdb_models(id) ON DELETE CASCADE,
    relation_type     VARCHAR(16)  NOT NULL,           -- 'belongs_to' | 'relates_to'
    relation_name     VARCHAR(128),                   -- 关系显示名：运行于、依赖于、关联
    description       TEXT,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (source_model_id, target_model_id, relation_type)
);

-- 公共选项库
CREATE TABLE cmdb_option_sets (
    id          BIGSERIAL PRIMARY KEY,
    name        VARCHAR(128) NOT NULL,                -- 显示名：资源状态、环境类型
    code        VARCHAR(64)  NOT NULL UNIQUE,          -- 编码：resource_status, env_type
    options     JSONB        NOT NULL,                -- [{"label":"运行中","value":"running","color":"green"},...]
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- 资源实例表（动态模型，通用字段 + fields JSONB）
-- provider 语义（附录 B #19）：托管厂商，K8s 集群 ack→aliyun / gke→gcp / 自建→k8s
CREATE TABLE cmdb_resources (
    id                BIGSERIAL PRIMARY KEY,
    model_id          BIGINT       NOT NULL REFERENCES cmdb_models(id),
    name              VARCHAR(256) NOT NULL,
    provider          VARCHAR(32),                    -- 'aliyun' | 'aws' | 'gcp' | 'k8s' | 'manual'
    provider_id       VARCHAR(256),                   -- 云厂商原始 ID（K8s: cluster/ns/name）
    cloud_account     VARCHAR(128),                   -- 云账号 ID（K8s: cluster_id）
    region            VARCHAR(64),
    zone              VARCHAR(64),
    status            VARCHAR(32)  DEFAULT 'unknown', -- running|stopped|maintenance|unknown；NULL = 资源类型无生命周期状态
    fields            JSONB        NOT NULL DEFAULT '{}', -- 动态字段（按模型定义过滤后落库）
    resource_version  VARCHAR(64),                    -- 幂等版本号（K8s resourceVersion）
    synced_at         TIMESTAMPTZ,
    source            VARCHAR(32)  NOT NULL DEFAULT 'manual',  -- 'manual' | 'discovery'
    deleted_at        TIMESTAMPTZ,                    -- 软删除
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (model_id, provider, provider_id, cloud_account)
);

CREATE INDEX idx_cmdb_resource_model     ON cmdb_resources (model_id);
CREATE INDEX idx_cmdb_resource_provider  ON cmdb_resources (provider);
CREATE INDEX idx_cmdb_resource_status    ON cmdb_resources (status);
CREATE INDEX idx_cmdb_resource_name      ON cmdb_resources (name);
CREATE INDEX idx_cmdb_resource_region    ON cmdb_resources (region);
CREATE INDEX idx_cmdb_resource_account   ON cmdb_resources (cloud_account);
CREATE INDEX idx_cmdb_resource_synced    ON cmdb_resources (synced_at);
CREATE INDEX idx_cmdb_resource_fields    ON cmdb_resources USING GIN (fields);

-- 业务应用表
CREATE TABLE cmdb_business_apps (
    id          BIGSERIAL PRIMARY KEY,
    app_code    VARCHAR(64)  NOT NULL UNIQUE,
    name        VARCHAR(256) NOT NULL,
    description TEXT,
    team        VARCHAR(128),
    owner       VARCHAR(128),
    department  VARCHAR(128),
    labels      JSONB        NOT NULL DEFAULT '{}',
    repo_url    VARCHAR(512),                          -- 代码仓库地址（研发资产坐标）
    pipelines   JSONB        NOT NULL DEFAULT '{}',    -- {环境: 流水线地址}，key 对齐 env 标签值域
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_cmdb_app_team  ON cmdb_business_apps (team);
CREATE INDEX idx_cmdb_app_owner ON cmdb_business_apps (owner);

-- 应用-资源显式关联表（#13 物化：tag 自动归集 + manual 手动绑定）
CREATE TABLE cmdb_app_resources (
    id          BIGSERIAL PRIMARY KEY,
    app_id      BIGINT       NOT NULL REFERENCES cmdb_business_apps(id) ON DELETE CASCADE,
    resource_id BIGINT       NOT NULL REFERENCES cmdb_resources(id) ON DELETE CASCADE,
    source      VARCHAR(16)  NOT NULL DEFAULT 'tag',
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (app_id, resource_id)
);

CREATE INDEX idx_cmdb_app_resource_resource ON cmdb_app_resources (resource_id);

-- 从属关系表（层级归属，树形结构）
CREATE TABLE cmdb_belongs_to (
    id              BIGSERIAL PRIMARY KEY,
    child_id        BIGINT       NOT NULL REFERENCES cmdb_resources(id) ON DELETE CASCADE,
    parent_id       BIGINT       NOT NULL REFERENCES cmdb_resources(id) ON DELETE CASCADE,
    description     VARCHAR(256),
    synced_at       TIMESTAMPTZ,
    source          VARCHAR(32)  NOT NULL DEFAULT 'discovery',
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (child_id, parent_id)
);

CREATE INDEX idx_cmdb_belongs_child  ON cmdb_belongs_to (child_id);
CREATE INDEX idx_cmdb_belongs_parent ON cmdb_belongs_to (parent_id);

-- 关联关系表（对等关联，图结构）
CREATE TABLE cmdb_relates_to (
    id              BIGSERIAL PRIMARY KEY,
    source_id       BIGINT       NOT NULL REFERENCES cmdb_resources(id) ON DELETE CASCADE,
    target_id       BIGINT       NOT NULL REFERENCES cmdb_resources(id) ON DELETE CASCADE,
    description     VARCHAR(256),
    kind            VARCHAR(32)  NOT NULL DEFAULT '',
    attributes      JSONB        NOT NULL DEFAULT '{}',
    synced_at       TIMESTAMPTZ,
    source          VARCHAR(32)  NOT NULL DEFAULT 'discovery',
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (source_id, target_id, kind)
);

CREATE INDEX idx_cmdb_relates_source ON cmdb_relates_to (source_id);
CREATE INDEX idx_cmdb_relates_target ON cmdb_relates_to (target_id);

-- 标签定义表
CREATE TABLE cmdb_tag_definitions (
    id              BIGSERIAL PRIMARY KEY,
    tag_key         VARCHAR(128) NOT NULL UNIQUE,
    name            VARCHAR(256) NOT NULL,
    description     TEXT,
    category        VARCHAR(32)  NOT NULL DEFAULT 'custom',
    value_type      VARCHAR(16)  NOT NULL DEFAULT 'string',
    allowed_values  JSONB,
    editable        BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- 资源标签关联表（无资源外键以外的审计约束；source: manual 手动优先，cloud 云侧可覆盖）
CREATE TABLE cmdb_resource_tags (
    id              BIGSERIAL PRIMARY KEY,
    resource_id     BIGINT       NOT NULL REFERENCES cmdb_resources(id) ON DELETE CASCADE,
    tag_key         VARCHAR(128) NOT NULL,
    tag_value       TEXT         NOT NULL,
    source          VARCHAR(16)  NOT NULL DEFAULT 'manual',
    raw_key         VARCHAR(256),
    synced_at       TIMESTAMPTZ,
    operator        VARCHAR(128),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (resource_id, tag_key, source)
);

CREATE INDEX idx_cmdb_tag_resource  ON cmdb_resource_tags (resource_id);
CREATE INDEX idx_cmdb_tag_key       ON cmdb_resource_tags (tag_key);
CREATE INDEX idx_cmdb_tag_value     ON cmdb_resource_tags (tag_value);
CREATE INDEX idx_cmdb_tag_key_value ON cmdb_resource_tags (tag_key, tag_value);
CREATE INDEX idx_cmdb_tag_source    ON cmdb_resource_tags (source);

-- 同步任务配置表（同步与否的唯一事实源：未配置或禁用 → 消费端直接跳过）
-- 同一 (task_type, target_id) 允许多个任务：按资源类型拆分独立调度（v8 放开唯一约束）
CREATE TABLE cmdb_sync_tasks (
    id              BIGSERIAL PRIMARY KEY,
    name            VARCHAR(256) NOT NULL,               -- 任务名称（显示用）
    task_type       VARCHAR(16)  NOT NULL,               -- 'k8s' | 'cloud'
    provider        VARCHAR(32),                         -- 云厂商: aliyun|aws|gcp（cloud 类型必填）
    target_id       VARCHAR(256) NOT NULL,               -- 目标标识: 集群ID 或 云账号ID
    resource_types  JSONB        NOT NULL DEFAULT '[]',  -- 资源类型白名单（空 = 全部类型）
    schedule        VARCHAR(64),                         -- cron 表达式（cloud 类型使用）
    enabled         BOOLEAN      NOT NULL DEFAULT TRUE,  -- 是否启用
    description     TEXT,
    last_synced_at  TIMESTAMPTZ,                         -- 最近一次同步时间
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_cmdb_sync_task_type    ON cmdb_sync_tasks (task_type);
CREATE INDEX idx_cmdb_sync_task_enabled ON cmdb_sync_tasks (enabled);
CREATE INDEX idx_cmdb_sync_task_target  ON cmdb_sync_tasks (target_id);

-- 变更记录表（审计需活过资源删除，故 resource_id 无外键）
CREATE TABLE cmdb_change_logs (
    id              BIGSERIAL PRIMARY KEY,
    resource_id     BIGINT       NOT NULL,
    model_id        BIGINT,                          -- 冗余模型 ID
    change_type     VARCHAR(16)  NOT NULL,           -- create | update | delete
    field           VARCHAR(128),
    old_value       TEXT,
    new_value       TEXT,
    source          VARCHAR(32)  NOT NULL DEFAULT 'manual',
    operator        VARCHAR(128),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_cmdb_change_resource ON cmdb_change_logs (resource_id);
CREATE INDEX idx_cmdb_change_model    ON cmdb_change_logs (model_id);
CREATE INDEX idx_cmdb_change_type     ON cmdb_change_logs (change_type);
CREATE INDEX idx_cmdb_change_time     ON cmdb_change_logs (created_at);

-- ============================================================================
-- 工单系统
-- ============================================================================

-- 工单主表
CREATE TABLE tickets (
    id                  BIGSERIAL PRIMARY KEY,
    ticket_no           VARCHAR(32)  NOT NULL UNIQUE,        -- 工单号（TK{id:08d}）
    title               VARCHAR(256) NOT NULL,
    description         TEXT,
    ticket_type         VARCHAR(32)  NOT NULL DEFAULT 'general',  -- general|request|change|incident
    status              VARCHAR(16)  NOT NULL DEFAULT 'open',     -- open|in_progress|resolved|closed|cancelled
    priority            VARCHAR(16)  NOT NULL DEFAULT 'medium',   -- low|medium|high|urgent
    creator_id          BIGINT       NOT NULL REFERENCES users(id),
    assignee_id         BIGINT       REFERENCES users(id),
    related_resource_id BIGINT       REFERENCES cmdb_resources(id) ON DELETE SET NULL,
    runbook_id          BIGINT       REFERENCES runbooks(id),   -- 下发时处理人选定的 runbook（执行工具）
    job_params          JSONB        NOT NULL DEFAULT '{}',
    code_ref            VARCHAR(128),                            -- git tag 快照（同 job_executions）
    approval_status     VARCHAR(16),                             -- none|pending|approved|rejected
    risk_level          VARCHAR(16)  NOT NULL DEFAULT 'low',     -- 事项 default_risk 快照，驱动审批门控
    catalog_item_id     BIGINT,       -- 服务目录事项（二级，FK 于表定义后补建）
    group_id            BIGINT,
    difficulty          VARCHAR(16),                             -- 建单时从目录快照 simple|medium|hard
    started_at          TIMESTAMPTZ,                             -- 开始处理时间（响应时长计算）
    target_resource_ids JSONB        NOT NULL DEFAULT '[]',      -- 执行目标资源 ID 列表（运维下发时填写，多选唯一入口）
    business_app_id     BIGINT       REFERENCES cmdb_business_apps(id), -- 关联业务应用（上下文；申请类开通归属）
    resolved_at         TIMESTAMPTZ,
    closed_at           TIMESTAMPTZ,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_ticket_status     ON tickets (status);
CREATE INDEX idx_ticket_type       ON tickets (ticket_type);
CREATE INDEX idx_ticket_priority   ON tickets (priority);
CREATE INDEX idx_ticket_creator    ON tickets (creator_id);
CREATE INDEX idx_ticket_assignee   ON tickets (assignee_id);
CREATE INDEX idx_ticket_created_at ON tickets (created_at DESC);

-- 工单流转/评论记录表（不可变，仅 created_at）
CREATE TABLE ticket_comments (
    id          BIGSERIAL PRIMARY KEY,
    ticket_id   BIGINT      NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    user_id     BIGINT      NOT NULL REFERENCES users(id),
    action      VARCHAR(16) NOT NULL,              -- create|comment|assign|status_change
    content     TEXT,
    from_value  VARCHAR(64),                       -- assign/status_change 的原值
    to_value    VARCHAR(64),                       -- assign/status_change 的新值
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_ticket_comment_ticket ON ticket_comments (ticket_id);
CREATE INDEX idx_ticket_comment_time   ON ticket_comments (created_at);

-- 工单审批记录表（不可变，仅 created_at；P3 审批挂接）
CREATE TABLE ticket_approvals (
    id          BIGSERIAL PRIMARY KEY,
    ticket_id   BIGINT      NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    approver_id BIGINT      NOT NULL REFERENCES users(id),
    action      VARCHAR(16) NOT NULL,              -- approve|reject
    comment     TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_ticket_approval_ticket ON ticket_approvals (ticket_id);

-- 变更封禁窗口（scope=NULL 表示全局；JSONB 数组限定 model_code）
CREATE TABLE change_freezes (
    id          BIGSERIAL PRIMARY KEY,
    name        VARCHAR(128) NOT NULL,
    reason      TEXT,
    scope       JSONB,
    starts_at   TIMESTAMPTZ  NOT NULL,
    ends_at     TIMESTAMPTZ  NOT NULL,
    created_by  BIGINT       REFERENCES users(id) ON DELETE SET NULL,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CHECK (ends_at > starts_at)
);

CREATE INDEX idx_change_freeze_time ON change_freezes (starts_at, ends_at);

-- 两级服务目录（parent_id=NULL 为一级分类；事项挂难度/默认风险/默认类型/默认 runbook）
CREATE TABLE ticket_catalog (
    id                 BIGSERIAL PRIMARY KEY,
    name               VARCHAR(128) NOT NULL UNIQUE,
    parent_id          BIGINT       REFERENCES ticket_catalog(id) ON DELETE CASCADE,
    description        TEXT,
    difficulty         VARCHAR(16)  NOT NULL DEFAULT 'simple',  -- simple|medium|hard
    default_risk       VARCHAR(16)  NOT NULL DEFAULT 'low',     -- low|medium|high
    default_type       VARCHAR(32)  NOT NULL DEFAULT 'request', -- 语义 ticket_type
    default_group_id   BIGINT       REFERENCES ticket_groups(id), -- 默认处理组（路由配置化）
    is_active          BOOLEAN      NOT NULL DEFAULT TRUE,
    sort_order         INT          NOT NULL DEFAULT 0,
    created_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_ticket_catalog_parent ON ticket_catalog (parent_id);

-- 工单处理组（派单/值班的最小单位，与 RBAC 角色解耦）
CREATE TABLE ticket_groups (
    id          BIGSERIAL PRIMARY KEY,
    name        VARCHAR(128) NOT NULL UNIQUE,
    description TEXT,
    members     JSONB        NOT NULL DEFAULT '[]',   -- 用户 ID 数组
    is_active   BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- 运维值班表（日期 × 组 × 三线支持；tier1 自动派单来源，tier3 变更审批来源）
CREATE TABLE oncall_schedules (
    id          BIGSERIAL PRIMARY KEY,
    group_id    BIGINT      NOT NULL REFERENCES ticket_groups(id) ON DELETE CASCADE,
    oncall_date DATE        NOT NULL,
    tier1       JSONB       NOT NULL DEFAULT '[]',
    tier2       JSONB       NOT NULL DEFAULT '[]',
    tier3       JSONB       NOT NULL DEFAULT '[]',
    note        TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (group_id, oncall_date)
);
CREATE INDEX idx_oncall_date ON oncall_schedules (oncall_date);

-- tickets 对目录/处理组的外键（表定义顺序原因，后置补建）
ALTER TABLE tickets
    ADD CONSTRAINT fk_tickets_catalog_item
    FOREIGN KEY (catalog_item_id) REFERENCES ticket_catalog(id);
ALTER TABLE tickets
    ADD CONSTRAINT fk_tickets_group
    FOREIGN KEY (group_id) REFERENCES ticket_groups(id);

-- ============================================================================
-- 任务系统（runbook + job 执行引擎，设计见 docs/task-system-design.md）
-- ============================================================================

CREATE TABLE runbooks (
    id            BIGSERIAL PRIMARY KEY,
    name          VARCHAR(128) NOT NULL UNIQUE,
    category      VARCHAR(64),
    description   TEXT,
    params_schema JSONB        NOT NULL DEFAULT '{}',
    steps         JSONB        NOT NULL DEFAULT '[]',
    connection    JSONB        NOT NULL DEFAULT '{}',   -- {ssh_user, ssh_key_ref, become, become_method, become_user}
    target_models JSONB        NOT NULL DEFAULT '["aliyun_ecs", "gcp_compute"]',
    version       INT          NOT NULL DEFAULT 1,
    risk_level    VARCHAR(16)  NOT NULL DEFAULT 'low',
    auto_rollback BOOLEAN      NOT NULL DEFAULT FALSE,
    is_active     BOOLEAN      NOT NULL DEFAULT TRUE,
    created_by    BIGINT       REFERENCES users(id) ON DELETE SET NULL,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE job_executions (
    id               BIGSERIAL PRIMARY KEY,
    runbook_id       BIGINT       NOT NULL REFERENCES runbooks(id),
    runbook_version  INT          NOT NULL,
    code_ref         VARCHAR(128) NOT NULL,
    params           JSONB        NOT NULL DEFAULT '{}',
    target_resources JSONB        NOT NULL DEFAULT '[]',
    steps_snapshot   JSONB        NOT NULL DEFAULT '[]',
    connection       JSONB        NOT NULL DEFAULT '{}',
    status           VARCHAR(32)  NOT NULL DEFAULT 'pending',
    rollback_policy  VARCHAR(16)  NOT NULL DEFAULT 'manual',
    ticket_id        BIGINT,
    triggered_by     BIGINT       NOT NULL REFERENCES users(id),
    started_at       TIMESTAMPTZ,
    finished_at      TIMESTAMPTZ,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_job_exec_status  ON job_executions (status);
CREATE INDEX idx_job_exec_runbook ON job_executions (runbook_id);

CREATE TABLE job_steps (
    id            BIGSERIAL PRIMARY KEY,
    execution_id  BIGINT      NOT NULL REFERENCES job_executions(id) ON DELETE CASCADE,
    step_key      VARCHAR(64) NOT NULL,
    step_name     VARCHAR(128),
    type          VARCHAR(16) NOT NULL DEFAULT 'ansible',
    attempt_type  VARCHAR(16) NOT NULL DEFAULT 'do',
    status        VARCHAR(32) NOT NULL DEFAULT 'pending',
    serial        VARCHAR(16),
    exit_code     INT,
    error_message TEXT,
    started_at    TIMESTAMPTZ,
    finished_at   TIMESTAMPTZ,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_job_step_key_attempt UNIQUE (execution_id, step_key, attempt_type)
);
CREATE INDEX idx_job_step_exec ON job_steps (execution_id);

CREATE TABLE job_step_logs (
    id        BIGSERIAL PRIMARY KEY,
    step_id   BIGINT      NOT NULL REFERENCES job_steps(id) ON DELETE CASCADE,
    seq       INT         NOT NULL,
    level     VARCHAR(16) NOT NULL DEFAULT 'info',
    host      VARCHAR(128),
    line      TEXT        NOT NULL,
    logged_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_job_step_log_seq UNIQUE (step_id, seq)
);
CREATE INDEX idx_job_log_step ON job_step_logs (step_id, seq);

-- ============================================================================
-- 触发器：自动维护 updated_at
-- ============================================================================

CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
DECLARE
    tbl TEXT;
BEGIN
    FOR tbl IN
        SELECT unnest(ARRAY[
            'users', 'roles', 'permissions',
            'cmdb_model_categories', 'cmdb_models', 'cmdb_model_fields',
            'cmdb_option_sets', 'cmdb_resources', 'cmdb_business_apps',
            'cmdb_tag_definitions', 'cmdb_resource_tags', 'cmdb_sync_tasks',
            'tickets',
            'runbooks', 'job_executions', 'job_steps',
            'change_freezes',
            'ticket_catalog', 'ticket_groups', 'oncall_schedules'
        ])
    LOOP
        EXECUTE format(
            'CREATE TRIGGER trg_%s_updated_at
             BEFORE UPDATE ON %I
             FOR EACH ROW EXECUTE FUNCTION update_updated_at()',
            tbl, tbl
        );
    END LOOP;
END $$;

-- ============================================================================
-- 种子数据：预置角色 + 权限
-- ============================================================================

-- 预置角色
INSERT INTO roles (code, name, description, is_system) VALUES
('admin',    '管理员',     '超级管理员，拥有所有权限',   TRUE),
('operator', '运维操作员', '可执行部署、管理主机等操作', TRUE),
('viewer',   '只读查看',   '只能查看资源，不能修改',     TRUE),
('auditor',  '审计员',     '可查看全部数据和审计日志',   TRUE)
ON CONFLICT (code) DO NOTHING;

-- 预置权限（host/deploy/playbook/credential/task 为保留码，对应模块 v2 落地后启用）
INSERT INTO permissions (code, name) VALUES
('host:list',          '查看主机列表'),
('host:get',           '查看主机详情'),
('host:create',        '创建主机'),
('host:update',        '更新主机'),
('host:delete',        '删除主机'),
('host_group:list',    '查看主机组列表'),
('host_group:create',  '创建主机组'),
('host_group:update',  '更新主机组'),
('host_group:delete',  '删除主机组'),
('deploy:list',        '查看部署列表'),
('deploy:get',         '查看部署详情'),
('deploy:execute',     '执行部署'),
('deploy:cancel',      '取消部署'),
('playbook:list',      '查看 Playbook 列表'),
('playbook:create',    '创建 Playbook'),
('playbook:update',    '更新 Playbook'),
('playbook:delete',    '删除 Playbook'),
('credential:list',    '查看凭据列表'),
('credential:create',  '创建凭据'),
('credential:update',  '更新凭据'),
('credential:delete',  '删除凭据'),
('task:list',          '查看任务列表'),
('task:get',           '查看任务详情'),
('task:create',        '创建任务'),
('task:cancel',        '取消任务'),
('user:list',          '查看用户列表'),
('user:create',        '创建用户'),
('user:update',        '更新用户'),
('user:delete',        '删除用户'),
('user:assign_role',   '分配角色'),
('role:list',          '查看角色列表'),
('role:create',        '创建角色'),
('role:update',        '更新角色'),
('role:delete',        '删除角色'),
('audit:list',         '查看审计日志'),
('audit:get',          '查看审计详情'),
('tag:list',           '查看标签列表'),
('tag:create',         '创建标签'),
('tag:update',         '更新标签'),
('tag:delete',         '删除标签'),
('cmdb_model:list',        '查看 CMDB 模型'),
('cmdb_model:create',      '创建 CMDB 模型'),
('cmdb_model:update',      '更新 CMDB 模型'),
('cmdb_model:delete',      '删除 CMDB 模型'),
('cmdb_resource:list',     '查看 CMDB 资源列表'),
('cmdb_resource:get',      '查看 CMDB 资源详情'),
('cmdb_resource:create',   '创建 CMDB 资源'),
('cmdb_resource:update',   '更新 CMDB 资源'),
('cmdb_resource:delete',   '删除 CMDB 资源'),
('cmdb_tag:list',          '查看 CMDB 标签'),
('cmdb_tag:create',        '创建 CMDB 标签'),
('cmdb_tag:update',        '更新 CMDB 标签'),
('cmdb_tag:delete',        '删除 CMDB 标签'),
('cmdb_app:list',          '查看 CMDB 业务应用'),
('cmdb_app:create',        '创建 CMDB 业务应用'),
('cmdb_app:update',        '更新 CMDB 业务应用'),
('cmdb_app:delete',        '删除 CMDB 业务应用'),
('cmdb_change:list',       '查看 CMDB 变更记录'),
('cmdb_sync_task:list',    '查看同步任务列表'),
('cmdb_sync_task:create',  '创建同步任务'),
('cmdb_sync_task:update',  '更新同步任务'),
('cmdb_sync_task:delete',  '删除同步任务'),
('ticket:list',    '查看工单'),
('ticket:create',  '创建工单'),
('ticket:update',  '更新工单'),
('ticket:assign',  '指派工单'),
('ticket:delete',  '删除工单'),
('ticket:approve', '审批工单'),
('runbook:list',   '查看 Runbook 列表'),
('runbook:get',    '查看 Runbook 详情'),
('runbook:create', '创建 Runbook'),
('runbook:update', '更新 Runbook'),
('runbook:delete', '删除 Runbook'),
('job:list',       '查看任务执行列表'),
('job:get',        '查看任务执行详情'),
('job:create',     '创建并下发任务'),
('job:cancel',     '取消任务'),
('job:rollback',   '回滚任务'),
('change_freeze:list',   '查看变更封禁窗口'),
('change_freeze:create', '创建变更封禁窗口'),
('change_freeze:delete', '删除变更封禁窗口'),
('ticket_catalog:list',   '查看服务目录'),
('ticket_catalog:create', '创建服务目录项'),
('ticket_catalog:update', '更新服务目录项'),
('ticket_catalog:delete', '删除服务目录项'),
('ticket_group:list',     '查看处理组'),
('ticket_group:create',   '创建处理组'),
('ticket_group:update',   '更新处理组'),
('ticket_group:delete',   '删除处理组'),
('oncall:list',           '查看值班表'),
('oncall:create',         '创建值班排班'),
('oncall:update',         '更新值班排班'),
('oncall:delete',         '删除值班排班')
ON CONFLICT (code) DO NOTHING;

-- admin 角色分配所有权限（须在全部权限插入后执行）
INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM roles r, permissions p
WHERE r.code = 'admin'
ON CONFLICT DO NOTHING;

-- operator 角色分配操作类权限（排除用户/角色管理）
INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM roles r, permissions p
WHERE r.code = 'operator'
  AND p.code NOT LIKE 'user:%'
  AND p.code NOT LIKE 'role:%'
ON CONFLICT DO NOTHING;

-- viewer 角色分配所有 list + get 权限
INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM roles r, permissions p
WHERE r.code = 'viewer'
  AND (p.code LIKE '%:list' OR p.code LIKE '%:get')
ON CONFLICT DO NOTHING;

-- auditor 角色分配所有 list + get + audit 权限
INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM roles r, permissions p
WHERE r.code = 'auditor'
  AND (p.code LIKE '%:list' OR p.code LIKE '%:get' OR p.code LIKE 'audit:%')
ON CONFLICT DO NOTHING;

-- ============================================================================
-- CMDB 预置数据
-- ============================================================================

-- 公共选项库预置数据
INSERT INTO cmdb_option_sets (code, name, options) VALUES
('resource_status', '资源状态', '[{"label":"运行中","value":"running","color":"green"},{"label":"已停止","value":"stopped","color":"red"},{"label":"维护中","value":"maintenance","color":"orange"},{"label":"未知","value":"unknown","color":"gray"}]'),
('env_type', '环境类型', '[{"label":"生产","value":"production"},{"label":"预发","value":"staging"},{"label":"测试","value":"test"},{"label":"开发","value":"dev"}]'),
('cloud_provider', '云厂商', '[{"label":"阿里云","value":"aliyun"},{"label":"AWS","value":"aws"},{"label":"GCP","value":"gcp"},{"label":"K8s","value":"k8s"}]')
ON CONFLICT (code) DO NOTHING;

-- 预置系统标签
INSERT INTO cmdb_tag_definitions (tag_key, name, category, value_type, allowed_values) VALUES
('env',   '环境',     'system', 'enum',   '["production","staging","dev","test"]'),
('app',   '归属应用', 'system', 'string', NULL),
('team',  '所属团队', 'system', 'string', NULL),
('owner', '负责人',   'system', 'string', NULL)
ON CONFLICT (tag_key) DO NOTHING;
