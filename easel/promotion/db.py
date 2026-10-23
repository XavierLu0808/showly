"""产品推广平台 SQLite 存储层。

- 表结构遵循《代码改造方案》第 2 节数据模型：Product / ProductVersion /
  ProductFact / Asset / Campaign / ContentVariant / ContentRevision / Review /
  GenerationJob / Publication / MetricRecord / TrackingLink。
- 通过 ``SCHEMA_MIGRATIONS`` 做增量迁移，数据库文件默认在
  ``outputs/_promotion/promotion.db``，可用环境变量 ``EASEL_PROMOTION_DB`` 覆盖
  （测试隔离用）。
- 连接为线程本地（后台任务线程与请求线程各自持有），WAL 模式，外键开启。
"""
from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
_LOCAL = threading.local()

# 迁移按顺序执行；新增结构变化时追加新版本，不修改历史语句。
SCHEMA_MIGRATIONS: list[str] = [
    # v1：第一版全量表结构
    """
    CREATE TABLE IF NOT EXISTS products (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        tagline TEXT NOT NULL DEFAULT '',
        website TEXT NOT NULL DEFAULT '',
        language TEXT NOT NULL DEFAULT 'zh-CN',
        logo_path TEXT NOT NULL DEFAULT '',
        brand_color TEXT NOT NULL DEFAULT '',
        default_persona TEXT NOT NULL DEFAULT '',
        forbidden_claims TEXT NOT NULL DEFAULT '',
        archived INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """
    CREATE TABLE IF NOT EXISTS product_versions (
        id TEXT PRIMARY KEY,
        product_id TEXT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
        version_label TEXT NOT NULL,
        fact_snapshot TEXT NOT NULL DEFAULT '[]',
        confirmed INTEGER NOT NULL DEFAULT 0,
        confirmed_at TEXT,
        created_at TEXT NOT NULL
    )""",
    """
    CREATE TABLE IF NOT EXISTS product_facts (
        id TEXT PRIMARY KEY,
        product_id TEXT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
        version_id TEXT REFERENCES product_versions(id) ON DELETE SET NULL,
        category TEXT NOT NULL,
        content TEXT NOT NULL,
        source TEXT NOT NULL DEFAULT 'manual',
        launch_status TEXT NOT NULL DEFAULT 'unverified',
        confirmed INTEGER NOT NULL DEFAULT 0,
        confirmed_at TEXT,
        created_at TEXT NOT NULL
    )""",
    """
    CREATE TABLE IF NOT EXISTS assets (
        id TEXT PRIMARY KEY,
        product_id TEXT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
        version_id TEXT REFERENCES product_versions(id) ON DELETE SET NULL,
        kind TEXT NOT NULL,
        name TEXT NOT NULL,
        path TEXT NOT NULL,
        note TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL DEFAULT 'user-upload',
        restrictions TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    )""",
    """
    CREATE TABLE IF NOT EXISTS campaigns (
        id TEXT PRIMARY KEY,
        product_id TEXT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
        version_id TEXT REFERENCES product_versions(id) ON DELETE SET NULL,
        template TEXT NOT NULL,
        goal TEXT NOT NULL DEFAULT 'visit',
        audience TEXT NOT NULL DEFAULT '',
        channels TEXT NOT NULL DEFAULT '[]',
        voice TEXT NOT NULL DEFAULT '',
        cta TEXT NOT NULL DEFAULT '',
        plan_date TEXT,
        brief TEXT NOT NULL DEFAULT '',
        brief_confirmed INTEGER NOT NULL DEFAULT 0,
        angles TEXT NOT NULL DEFAULT '[]',
        tasks TEXT NOT NULL DEFAULT '[]',
        status TEXT NOT NULL DEFAULT 'draft',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """
    CREATE TABLE IF NOT EXISTS content_variants (
        id TEXT PRIMARY KEY,
        campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
        platform TEXT NOT NULL,
        format TEXT NOT NULL,
        angle TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'draft',
        current_revision INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """
    CREATE TABLE IF NOT EXISTS content_revisions (
        id TEXT PRIMARY KEY,
        content_id TEXT NOT NULL REFERENCES content_variants(id) ON DELETE CASCADE,
        revision_no INTEGER NOT NULL,
        title TEXT NOT NULL DEFAULT '',
        body TEXT NOT NULL DEFAULT '',
        structure TEXT NOT NULL DEFAULT '{}',
        asset_refs TEXT NOT NULL DEFAULT '[]',
        input_snapshot TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        UNIQUE(content_id, revision_no)
    )""",
    """
    CREATE TABLE IF NOT EXISTS reviews (
        id TEXT PRIMARY KEY,
        revision_id TEXT NOT NULL REFERENCES content_revisions(id) ON DELETE CASCADE,
        content_id TEXT NOT NULL,
        checks TEXT NOT NULL DEFAULT '[]',
        approved INTEGER NOT NULL DEFAULT 0,
        approved_by TEXT NOT NULL DEFAULT '',
        approved_at TEXT,
        invalidated_at TEXT,
        created_at TEXT NOT NULL
    )""",
    """
    CREATE TABLE IF NOT EXISTS generation_jobs (
        id TEXT PRIMARY KEY,
        campaign_id TEXT,
        content_id TEXT,
        job_type TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'queued',
        params TEXT NOT NULL DEFAULT '{}',
        steps TEXT NOT NULL DEFAULT '[]',
        artifacts TEXT NOT NULL DEFAULT '[]',
        error TEXT NOT NULL DEFAULT '',
        idempotency_key TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        finished_at TEXT
    )""",
    """
    CREATE TABLE IF NOT EXISTS publications (
        id TEXT PRIMARY KEY,
        content_id TEXT NOT NULL REFERENCES content_variants(id) ON DELETE CASCADE,
        revision_id TEXT REFERENCES content_revisions(id) ON DELETE SET NULL,
        campaign_id TEXT NOT NULL,
        channel TEXT NOT NULL,
        account TEXT NOT NULL DEFAULT '',
        planned_at TEXT,
        published_at TEXT,
        link TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'exported',
        confirm_source TEXT NOT NULL DEFAULT 'manual',
        note TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    )""",
    """
    CREATE TABLE IF NOT EXISTS metric_records (
        id TEXT PRIMARY KEY,
        campaign_id TEXT NOT NULL,
        content_id TEXT,
        platform TEXT NOT NULL DEFAULT '',
        metric TEXT NOT NULL,
        value REAL NOT NULL,
        unit TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL DEFAULT 'manual',
        window_start TEXT NOT NULL DEFAULT '',
        window_end TEXT NOT NULL DEFAULT '',
        import_batch TEXT NOT NULL DEFAULT '',
        recorded_at TEXT NOT NULL
    )""",
    """
    CREATE TABLE IF NOT EXISTS tracking_links (
        id TEXT PRIMARY KEY,
        campaign_id TEXT NOT NULL,
        content_id TEXT,
        target_url TEXT NOT NULL,
        utm TEXT NOT NULL DEFAULT '{}',
        full_url TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
]


def default_db_path() -> Path:
    override = os.environ.get("EASEL_PROMOTION_DB", "").strip()
    if override:
        return Path(override)
    return PROJECT_ROOT / "outputs" / "_promotion" / "promotion.db"


def db_path() -> Path:
    """当前生效的数据库路径（创建父目录）。"""
    path = default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _new_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path(), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def connect() -> sqlite3.Connection:
    """获取当前线程的连接（线程本地，WAL，外键开启）；首次自动建表迁移。"""
    if not getattr(_LOCAL, "initialized", False):
        _migrate(_new_connection())
        _LOCAL.initialized = True
    conn = getattr(_LOCAL, "conn", None)
    if conn is None:
        conn = _new_connection()
        _LOCAL.conn = conn
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY)"
    )
    applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
    for version, statement in enumerate(SCHEMA_MIGRATIONS, start=1):
        if version in applied:
            continue
        conn.executescript(statement)
        conn.execute("INSERT INTO schema_migrations (version) VALUES (?)", (version,))
    conn.commit()


def init_db(path: Path | None = None) -> None:
    """建库并执行增量迁移。``path`` 用于测试指定独立数据库。"""
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        os.environ["EASEL_PROMOTION_DB"] = str(path)
        # 切换数据库后，丢弃当前线程旧连接与初始化标记
        reset_thread_connection()
    _migrate(connect())


def reset_thread_connection() -> None:
    """显式关闭当前线程连接与初始化标记（测试清理/切库用）。"""
    conn = getattr(_LOCAL, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except sqlite3.Error:
            pass
        del _LOCAL.conn
    _LOCAL.initialized = False
