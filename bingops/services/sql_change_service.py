"""SQL 变更工单静态校验（P1 工单侧）。

职责边界：
- 只做**解析与预审**：高危语法拦截、语句类型识别、表名提取、可逆性判定、hash 计算。
- **不生成 DML 回滚语句**——回滚值取决于运行时命中的行，以 binlog before image 为唯一事实源。

AST 依据 sqlglot 29（MySQL 方言）：
- `CREATE TABLE/INDEX` → `exp.Create(kind=TABLE/INDEX)`
- `ALTER` → `exp.Alter(actions=[ColumnDef|Drop|AlterColumn|RenameColumn|AddConstraint])`
- `RENAME TABLE` 不被 sqlglot 解析，回退为 `exp.Command`，按 manual 处理
- `UPDATE/DELETE` 的 WHERE 通过 `node.args["where"]` 判定（含 `WHERE 1=1` 恒真绕过检查）
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

from bingops.core.exceptions import ValidationError

logger = logging.getLogger(f"bingops.{__name__}")

DIALECT = "mysql"
MAX_STATEMENTS = 1
VALID_SCOPES = ("dml", "ddl", "both")

# WHERE 恒真等价形式：视为"没有有效过滤条件"
_TAUTOLOGY_WHERE = {"1=1", "1 = 1", "(1=1)", "(1 = 1)", "true", "1", "(1)"}

# ALTER 中可自动反向的动作类型（ADD COLUMN / ADD INDEX / RENAME COLUMN）
_REVERSIBLE_ALTER_ACTIONS = (exp.ColumnDef, exp.AddConstraint, exp.RenameColumn)


@dataclass
class SqlChangeCheck:
    """校验结果（供建单落库与前端展示）。"""

    sql_hash: str
    statement_type: str
    tables: list[str] = field(default_factory=list)
    rollback_kind: str = "auto"  # auto|manual|none

    def describe(self) -> str:
        return f"{self.statement_type} on {', '.join(self.tables) or '(unknown)'}"


def _normalize(sql: str) -> str:
    """规范化 SQL（去注释/统一空白/统一大小写关键字），用于 hash 与审批执行比对。"""
    try:
        statements = sqlglot.parse(sql, read=DIALECT)
        rendered = ";\n".join(
            stmt.sql(dialect=DIALECT) for stmt in statements if stmt is not None
        )
        if rendered.strip():
            return rendered.strip()
    except Exception:  # 解析失败时退化为纯空白归一
        pass
    return " ".join((sql or "").split())


def sql_hash_of(sql: str) -> str:
    """变更 SQL 的规范化哈希（审批↔执行一致性锚点）。"""
    return hashlib.sha256(_normalize(sql).encode("utf-8")).hexdigest()


def _tables_of(node: exp.Expression) -> list[str]:
    return sorted({t.name for t in node.find_all(exp.Table) if t.name})


def _has_meaningful_where(node: exp.Expression) -> bool:
    where = node.args.get("where")
    if where is None:
        return False
    inner = where.this
    rendered = (inner.sql(dialect=DIALECT) if inner is not None else "").strip().lower()
    return rendered not in _TAUTOLOGY_WHERE


def _statement_type(node: exp.Expression) -> str:
    mapping = (
        (exp.Insert, "insert"),
        (exp.Update, "update"),
        (exp.Delete, "delete"),
        (exp.Create, "create"),
        (exp.Alter, "alter"),
        (exp.Drop, "drop"),
        (exp.TruncateTable, "truncate"),
    )
    for cls, name in mapping:
        if isinstance(node, cls):
            return name
    if isinstance(node, exp.Command):
        return "command"
    return node.key.lower()


def _alter_is_reversible(node: exp.Alter) -> bool:
    actions = node.args.get("actions") or []
    if not actions:
        return False
    return all(isinstance(action, _REVERSIBLE_ALTER_ACTIONS) for action in actions)


def check_change_sql(sql: str, *, sql_scope: str = "both") -> SqlChangeCheck:
    """校验变更 SQL；违规直接抛 ValidationError（422），合规返回预审结果。

    规则（对应 docs/ticket-system.md §18.4）：
    - 语句数超限、解析失败 → 拒绝
    - DROP DATABASE/SCHEMA、TRUNCATE、GRANT/REVOKE/SET 等非白名单语句 → 拒绝
    - UPDATE/DELETE 无有效 WHERE（含 1=1 恒真） → 拒绝
    - DROP COLUMN / 改类型 / DROP TABLE / RENAME TABLE → rollback_kind=manual
    - CREATE TABLE/INDEX、ADD COLUMN、ADD INDEX、RENAME COLUMN → rollback_kind=auto
    - DML（INSERT/带 WHERE 的 UPDATE/DELETE） → rollback_kind=auto（执行时由 binlog 反解）
    """
    scope = sql_scope if sql_scope in VALID_SCOPES else "both"
    raw = (sql or "").strip()
    if not raw:
        raise ValidationError("change_sql is required")

    try:
        statements = [s for s in sqlglot.parse(raw, read=DIALECT) if s is not None]
    except Exception as exc:  # sqlglot 解析异常类型随版本变化，统一收敛
        raise ValidationError(f"SQL 解析失败：{exc}") from exc

    if not statements:
        raise ValidationError("change_sql is empty")
    if len(statements) > MAX_STATEMENTS:
        raise ValidationError(
            f"一次变更只允许 {MAX_STATEMENTS} 条语句（当前 {len(statements)} 条），请拆分提单",
        )

    node = statements[0]
    stmt = _statement_type(node)

    if isinstance(node, exp.Drop) and (node.args.get("kind") or "").upper() in (
        "DATABASE", "SCHEMA",
    ):
        raise ValidationError("DROP DATABASE/SCHEMA 已被拦截，如需此类操作请走人工审批流程")
    if isinstance(node, exp.TruncateTable):
        raise ValidationError("TRUNCATE 已拦截（数据不可回滚），请改用带 WHERE 的 DELETE")

    is_dml = stmt in ("insert", "update", "delete")
    is_ddl = stmt in ("create", "alter", "drop", "command")  # command: RENAME TABLE 等

    if not (is_dml or is_ddl):
        raise ValidationError(f"不支持的语句类型：{stmt}（仅允许 DML 与 DDL 变更语句）")
    if scope == "dml" and not is_dml:
        raise ValidationError(f"该事项仅允许 DML 变更（当前语句：{stmt}）")
    if scope == "ddl" and not is_ddl:
        raise ValidationError(f"该事项仅允许 DDL 变更（当前语句：{stmt}）")

    if is_dml and stmt in ("update", "delete") and not _has_meaningful_where(node):
        raise ValidationError(
            f"{stmt.upper()} 缺少有效 WHERE 条件已拦截（全表变更加载致命），请补充过滤条件",
        )

    if is_dml:
        rollback_kind = "auto"  # 执行后由 binlog 反解生成
    elif isinstance(node, exp.Create):
        kind = (node.args.get("kind") or "").upper()
        rollback_kind = "auto" if kind in ("TABLE", "INDEX") else "manual"
    elif isinstance(node, exp.Alter):
        rollback_kind = "auto" if _alter_is_reversible(node) else "manual"
    elif isinstance(node, exp.Drop):
        kind = (node.args.get("kind") or "").upper()
        rollback_kind = "auto" if kind == "INDEX" else "manual"  # DROP TABLE 需备份恢复
    else:
        rollback_kind = "manual"  # RENAME TABLE 等 Command 类

    result = SqlChangeCheck(
        sql_hash=sql_hash_of(raw),
        statement_type=stmt,
        tables=_tables_of(node),
        rollback_kind=rollback_kind,
    )
    logger.info(
        "SQL change precheck passed",
        extra={
            "statement_type": result.statement_type,
            "tables": result.tables,
            "rollback_kind": result.rollback_kind,
            "sql_scope": scope,
        },
    )
    return result
