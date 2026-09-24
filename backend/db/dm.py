"""达梦 DM8 数据库客户端（dmPython 封装）。

- 参数化查询，杜绝 SQL 注入；口令走 .env 不硬编码。
- 返回 list[dict]（列名小写），方便上层与 JSON 序列化。
- 连接按需建立、用完即关（数据量与并发都不大，先不做连接池）。

dmPython 由达梦官方驱动提供，服务器 /data/dmdbms/drivers/python/dmPython 已安装。
若 import 失败：cd 该目录后 `python3 setup.py install`。
"""
from __future__ import annotations

from typing import Any, Sequence

import dmPython  # 达梦官方驱动

from backend.config import Settings, get_settings


class DMClient:
    """轻量 DM8 访问封装。"""

    def __init__(self, settings: Settings | None = None) -> None:
        self._s = settings or get_settings()

    def _connect(self) -> "dmPython.Connection":
        return dmPython.connect(
            user=self._s.dm_user,
            password=self._s.dm_password,
            server=self._s.dm_host,
            port=self._s.dm_port,
        )

    def query(self, sql: str, params: Sequence[Any] | None = None) -> list[dict[str, Any]]:
        """执行查询，返回 list[dict]（列名小写）。params 用于参数化占位符 ?。"""
        conn = self._connect()
        try:
            cur = conn.cursor()
            try:
                cur.execute(sql, params or [])
                cols = [d[0].lower() for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
            finally:
                cur.close()
        finally:
            conn.close()

    def query_one(self, sql: str, params: Sequence[Any] | None = None) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> int:
        """执行单条写操作（INSERT / UPDATE / DELETE），返回受影响行数。

        参数化占位符 ?，杜绝 SQL 注入。提交成功后返回 rowcount；
        异常时回滚并向上抛出，由上层转成统一错误结构。
        """
        conn = self._connect()
        try:
            cur = conn.cursor()
            try:
                cur.execute(sql, params or [])
                affected = cur.rowcount
                conn.commit()
                return affected
            except Exception:
                conn.rollback()
                raise
            finally:
                cur.close()
        finally:
            conn.close()

    def execute_many(self, statements: Sequence[tuple[str, Sequence[Any]]]) -> int:
        """在同一事务内按顺序执行多条写操作，全部成功才提交，任一失败整体回滚。

        用于主表 + 详情表需保持一致性的场景。statements 为 (sql, params) 序列。
        返回受影响总行数。
        """
        conn = self._connect()
        try:
            cur = conn.cursor()
            try:
                total = 0
                for sql, params in statements:
                    cur.execute(sql, list(params) if params else [])
                    total += cur.rowcount
                conn.commit()
                return total
            except Exception:
                conn.rollback()
                raise
            finally:
                cur.close()
        finally:
            conn.close()

    def ping(self) -> bool:
        """连通性自检。"""
        return self.query_one("SELECT 1 AS ok") is not None


_client: DMClient | None = None


def get_dm() -> DMClient:
    """进程级单例。"""
    global _client
    if _client is None:
        _client = DMClient()
    return _client
