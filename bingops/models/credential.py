"""凭据目录 ORM 模型（平台级：只存 Vault 引用与元数据，永不存明文）。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from bingops.models.base import Base, BaseMixin

# 凭据类型：决定 runner 以哪种方式消费，也决定它能被哪个字段引用
CREDENTIAL_KINDS = ("ssh_key", "cloud_ak", "db_password", "api_token", "kubeconfig")

# Vault 可达性探测状态：由 runner 回填（bingops 不直连 Vault，见设计文档 §5）
VERIFY_STATES = ("unknown", "ok", "failed")

# 主机标签里引用凭据的 tag_key（tag_value = credentials.name）：
# 任务不再携带 SSH 凭据，机器在哪、钥匙在哪都由这里回答
HOST_CREDENTIAL_TAG_KEY = "ssh_credential"


class Credential(BaseMixin, Base):
    """凭据目录条目：把「哪把钥匙、属于谁、能干什么」收敛成可下拉选择的实体。

    存在的意义是消灭自由文本的凭据引用——手打 Vault 路径打错了，要等 runner
    异步取值失败才暴露；改成从目录里选，不存在的选项在表单上就选不出来。

    纪律：本表只存 Vault 路径与元数据，**任何字段都不得存明文凭据值**；
    平台不解析 vault_path 的内容，取真值是 runner 的唯一出口。
    """

    __tablename__ = "credentials"

    # 全局唯一：主机标签等引用点是裸字符串，重名会产生歧义
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    # 该凭据对应的系统用户（ssh_key 用）；选钥匙顺带定身份，任务不必再填一遍
    login_user: Mapped[str | None] = mapped_column(String(64), nullable=True)
    vault_path: Mapped[str] = mapped_column(String(512), nullable=False)
    # KV v2 的字段名（引用形如 path#field 时拆出来存这列）
    vault_field: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # 适用范围（可空 = 不限）：解析主机凭据时用于唯一匹配
    cloud_account: Mapped[str | None] = mapped_column(String(128), nullable=True)
    region: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 同 kind 下的兜底凭据（唯一性由部分索引保证）
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    verify_state: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
