import json
from datetime import datetime, timezone

from app.db import 夹具目录, 连接

# 数据表分组（勾稽、触发器、迁移共用的唯一出处）
枢纽表 = ("枢纽_号卡", "枢纽_套餐", "枢纽_客户")
链接表 = ("链接_号卡套餐", "链接_号卡客户")
# (卫星表, 父哈希列, 父表)
卫星组 = (
    ("卫星_号卡属性", "号卡哈希", "枢纽_号卡"),
    ("卫星_套餐属性", "套餐哈希", "枢纽_套餐"),
    ("卫星_客户属性", "客户哈希", "枢纽_客户"),
    ("卫星_订购属性", "链接哈希", "链接_号卡套餐"),
)
卫星表 = tuple(x[0] for x in 卫星组)
数据表 = 枢纽表 + 链接表 + 卫星表
# 勾稽可解释性所需的父表快照
快照表 = 枢纽表 + ("链接_号卡套餐",)

建表 = """
CREATE TABLE IF NOT EXISTS 站规 (
  键 TEXT PRIMARY KEY,
  值 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 装载批次 (
  编号 TEXT PRIMARY KEY,
  装载日 TEXT NOT NULL,
  时刻 TEXT NOT NULL,
  报告 TEXT NOT NULL DEFAULT '{}',
  状态 TEXT NOT NULL DEFAULT '成功'
);
CREATE TABLE IF NOT EXISTS 批次勾稽 (
  批号 TEXT PRIMARY KEY,
  时刻 TEXT NOT NULL,
  回执枢纽 INTEGER NOT NULL,
  回执链接 INTEGER NOT NULL,
  回执卫星 INTEGER NOT NULL,
  库增枢纽 INTEGER NOT NULL,
  库增链接 INTEGER NOT NULL,
  库增卫星 INTEGER NOT NULL,
  不可解释父子 TEXT NOT NULL,
  valid INTEGER NOT NULL,
  口径 TEXT NOT NULL,
  说明 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 枢纽_号卡 (
  哈希键 TEXT PRIMARY KEY,
  业务键 TEXT NOT NULL UNIQUE,
  装载日 TEXT NOT NULL,
  批号 TEXT
);
CREATE TABLE IF NOT EXISTS 枢纽_套餐 (
  哈希键 TEXT PRIMARY KEY,
  业务键 TEXT NOT NULL UNIQUE,
  装载日 TEXT NOT NULL,
  批号 TEXT
);
CREATE TABLE IF NOT EXISTS 枢纽_客户 (
  哈希键 TEXT PRIMARY KEY,
  业务键 TEXT NOT NULL UNIQUE,
  装载日 TEXT NOT NULL,
  批号 TEXT
);
CREATE TABLE IF NOT EXISTS 链接_号卡套餐 (
  哈希键 TEXT PRIMARY KEY,
  号卡哈希 TEXT NOT NULL REFERENCES 枢纽_号卡(哈希键),
  套餐哈希 TEXT NOT NULL REFERENCES 枢纽_套餐(哈希键),
  装载日 TEXT NOT NULL,
  批号 TEXT
);
CREATE TABLE IF NOT EXISTS 链接_号卡客户 (
  哈希键 TEXT PRIMARY KEY,
  号卡哈希 TEXT NOT NULL REFERENCES 枢纽_号卡(哈希键),
  客户哈希 TEXT NOT NULL REFERENCES 枢纽_客户(哈希键),
  装载日 TEXT NOT NULL,
  批号 TEXT
);
CREATE TABLE IF NOT EXISTS 卫星_号卡属性 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  号卡哈希 TEXT NOT NULL REFERENCES 枢纽_号卡(哈希键),
  装载日 TEXT NOT NULL,
  载荷哈希 TEXT NOT NULL,
  属性 TEXT NOT NULL,
  是否当前 INTEGER NOT NULL DEFAULT 1,
  批号 TEXT
);
CREATE TABLE IF NOT EXISTS 卫星_套餐属性 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  套餐哈希 TEXT NOT NULL REFERENCES 枢纽_套餐(哈希键),
  装载日 TEXT NOT NULL,
  载荷哈希 TEXT NOT NULL,
  属性 TEXT NOT NULL,
  是否当前 INTEGER NOT NULL DEFAULT 1,
  批号 TEXT
);
CREATE TABLE IF NOT EXISTS 卫星_客户属性 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  客户哈希 TEXT NOT NULL REFERENCES 枢纽_客户(哈希键),
  装载日 TEXT NOT NULL,
  载荷哈希 TEXT NOT NULL,
  属性 TEXT NOT NULL,
  是否当前 INTEGER NOT NULL DEFAULT 1,
  批号 TEXT
);
CREATE TABLE IF NOT EXISTS 卫星_订购属性 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  链接哈希 TEXT NOT NULL REFERENCES 链接_号卡套餐(哈希键),
  装载日 TEXT NOT NULL,
  载荷哈希 TEXT NOT NULL,
  属性 TEXT NOT NULL,
  是否当前 INTEGER NOT NULL DEFAULT 1,
  批号 TEXT
);
CREATE TABLE IF NOT EXISTS 隔离匣 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  原因 TEXT NOT NULL,
  载荷 TEXT NOT NULL,
  时刻 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 金库贯验 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  时刻 TEXT NOT NULL,
  枢纽数 INTEGER NOT NULL,
  链接数 INTEGER NOT NULL,
  卫星当前数 INTEGER NOT NULL,
  是否通过 INTEGER NOT NULL,
  说明 TEXT NOT NULL
);
"""


def _封账触发器() -> str:
    """成功封账批次的标签行禁止旁路插/删/改。

    卫星表唯一放行：开新版时把同身份旧当前行 是否当前 1→0（装载序闸内的正常关版）。
    """
    块 = []
    for 表 in 枢纽表 + 链接表:
        块 += [
            f"CREATE TRIGGER IF NOT EXISTS 禁插_{表} AFTER INSERT ON {表} "
            f"WHEN EXISTS (SELECT 1 FROM 装载批次 b WHERE b.编号=NEW.批号 AND b.状态='成功') "
            f"BEGIN SELECT RAISE(ABORT,'批号已封账，禁止旁路写入:{表}'); END;",
            f"CREATE TRIGGER IF NOT EXISTS 禁删_{表} BEFORE DELETE ON {表} "
            f"WHEN EXISTS (SELECT 1 FROM 装载批次 b WHERE b.编号=OLD.批号 AND b.状态='成功') "
            f"BEGIN SELECT RAISE(ABORT,'批号已封账，禁止旁路删改:{表}'); END;",
            f"CREATE TRIGGER IF NOT EXISTS 禁改_{表} BEFORE UPDATE ON {表} "
            f"WHEN EXISTS (SELECT 1 FROM 装载批次 b WHERE b.编号=OLD.批号 AND b.状态='成功') "
            f"BEGIN SELECT RAISE(ABORT,'批号已封账，禁止旁路删改:{表}'); END;",
        ]
    for 表, 父列, _ in 卫星组:
        放行关版 = (
            f"NEW.编号=OLD.编号 AND NEW.{父列}=OLD.{父列} AND NEW.装载日=OLD.装载日 "
            f"AND NEW.载荷哈希=OLD.载荷哈希 AND NEW.属性=OLD.属性 AND NEW.批号 IS OLD.批号 "
            f"AND OLD.是否当前=1 AND NEW.是否当前=0"
        )
        块 += [
            f"CREATE TRIGGER IF NOT EXISTS 禁插_{表} AFTER INSERT ON {表} "
            f"WHEN EXISTS (SELECT 1 FROM 装载批次 b WHERE b.编号=NEW.批号 AND b.状态='成功') "
            f"BEGIN SELECT RAISE(ABORT,'批号已封账，禁止旁路写入:{表}'); END;",
            f"CREATE TRIGGER IF NOT EXISTS 禁删_{表} BEFORE DELETE ON {表} "
            f"WHEN EXISTS (SELECT 1 FROM 装载批次 b WHERE b.编号=OLD.批号 AND b.状态='成功') "
            f"BEGIN SELECT RAISE(ABORT,'批号已封账，禁止旁路删改:{表}'); END;",
            f"CREATE TRIGGER IF NOT EXISTS 禁改_{表} BEFORE UPDATE ON {表} "
            f"WHEN EXISTS (SELECT 1 FROM 装载批次 b WHERE b.编号=OLD.批号 AND b.状态='成功') "
            f"AND NOT ({放行关版}) "
            f"BEGIN SELECT RAISE(ABORT,'批号已封账，禁止旁路删改:{表}'); END;",
        ]
    return "\n".join(块)


def _迁移(conn) -> None:
    """老库幂等迁移：补批号标签列、批状态、勾稽表、封账触发器。"""

    def 已有列(表: str) -> set[str]:
        return {r["name"] for r in conn.execute(f"PRAGMA table_info({表})")}

    for 表 in 数据表:
        if "批号" not in 已有列(表):
            conn.execute(f"ALTER TABLE {表} ADD COLUMN 批号 TEXT")
    if "状态" not in 已有列("装载批次"):
        conn.execute("ALTER TABLE 装载批次 ADD COLUMN 状态 TEXT NOT NULL DEFAULT '成功'")
    conn.executescript(_封账触发器())

    # 迁移前历史批次无批号标签可溯，补占位勾稽记录，不冒充按笔对账
    缺 = conn.execute(
        "SELECT b.编号 FROM 装载批次 b "
        "WHERE NOT EXISTS (SELECT 1 FROM 批次勾稽 g WHERE g.批号=b.编号)"
    ).fetchall()
    for r in 缺:
        conn.execute(
            "INSERT OR IGNORE INTO 批次勾稽"
            "(批号, 时刻, 回执枢纽, 回执链接, 回执卫星, 库增枢纽, 库增链接, 库增卫星, "
            " 不可解释父子, valid, 口径, 说明) "
            "VALUES (?,?,0,0,0,0,0,0,'[]',1,?,?)",
            (
                r["编号"],
                此刻(),
                "迁移前-按批勾稽不适用",
                "迁移前历史批次，行级无批号标签，不参与新口径按笔对账",
            ),
        )


def 此刻() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def 哈希键(*parts: str) -> str:
    import hashlib

    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:20]


def 载荷哈希(obj) -> str:
    import hashlib

    s = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def 初始化库() -> None:
    conn = 连接()
    try:
        conn.executescript(建表)
        _迁移(conn)
        if conn.execute("SELECT COUNT(*) AS c FROM 站规").fetchone()["c"] == 0:
            for k, v in {"站名": "号枢", "默认装载日": "2026-11-21"}.items():
                conn.execute("INSERT INTO 站规(键, 值) VALUES (?, ?)", (k, v))
        conn.commit()
    finally:
        conn.close()
