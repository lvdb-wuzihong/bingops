"""应用配置管理。

使用 pydantic-settings 从环境变量加载配置，禁止硬编码。
环境变量前缀：BINGOPS_
"""

from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """应用全局配置。"""

    model_config = {"env_prefix": "BINGOPS_", "env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    # 应用
    debug: bool = False
    log_level: str = "INFO"

    # CORS：逗号分隔来源列表；"*" 放开全部（JWT 走 Authorization 头，无需凭据模式）
    cors_origins: str = "*"

    # 日志文件输出：空字符串=不落盘（仅 stdout）；配置后按天轮转+gzip 压缩
    log_dir: str = ""
    log_retention_days: int = 7

    # 数据库
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/bingops"

    # Redis
    redis_url: str = "redis://localhost:6379"
    redis_password: str = ""
    redis_db: int = 0

    # JWT
    secret_key: str = "change-me-in-production"
    access_token_expire_minutes: int = 180
    refresh_token_expire_days: int = 7
    jwt_algorithm: str = "HS256"

    # Kafka
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_consumer_group: str = "bingops-cmdb"
    kafka_k8s_topic_pattern: str = "k8s-events-{cluster_id}"
    kafka_cloud_topic_pattern: str = "cloud-sync-{provider}"
    kafka_enabled: bool = False

    # MCP（AI agent 数据面，设计见 docs/ai-agent-mcp-design.md）
    mcp_enabled: bool = False        # 挂载 /mcp 端点（streamable-http，无状态模式）
    mcp_write_enabled: bool = False  # 写工具总开关（默认关闭，见 mcp/tools/writes.py）
    mcp_agent_user_id: int = 0       # agent 系统账号（写工具操作者；0 = 未配置）
    # DNS rebinding 防护（SDK 层 Host 校验）：生产必须把实际域名/服务名加进 allowed_hosts
    mcp_dns_rebinding_protection: bool = True
    mcp_allowed_hosts: str = "localhost:*,127.0.0.1:*,[::1]:*"  # 逗号分隔，支持 :* 端口通配
    mcp_allowed_origins: str = ""  # 逗号分隔（仅浏览器类客户端携带 Origin 时校验；agent 可留空）


class FeishuSettings(BaseSettings):
    """飞书 SSO 配置。"""

    model_config = {"env_prefix": "BINGOPS_FEISHU_", "env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    app_id: str = ""
    app_secret: str = ""
    redirect_uri: str = ""

    # 飞书 OAuth2 端点（固定值）
    authorize_url: str = "https://open.feishu.cn/open-apis/authen/v1/authorize"
    app_token_url: str = "https://open.feishu.cn/open-apis/auth/v3/app_access_token/internal"
    user_token_url: str = "https://open.feishu.cn/open-apis/authen/v1/oidc/access_token"
    user_info_url: str = "https://open.feishu.cn/open-apis/authen/v1/user_info"


settings = Settings()
feishu_settings = FeishuSettings()
