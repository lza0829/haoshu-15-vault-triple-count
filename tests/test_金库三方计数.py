#!/usr/bin/env python3
"""金库三方计数——按批勾稽（口径乙）夹具测试。

运行（仓库根目录，隔离模式）：
  python3 -I tests/test_金库三方计数.py

覆盖：
  T0 新库建账 + 老库幂等迁移
  T1 正常批通过（含第二批复用装载前父键可解释）
  T2 三计数相等但混入父不可解脏链：口径甲会过、口径乙必须失败闭
  T3 对账/封账并发旁路：写锁互斥 + 封账触发器，回执成功与按批增量不分叉
  T4 库增被旁路撑大导致三计数分叉：失败闭
  T5 只读贯验接口字段：批号、三计数、不可解释父子、valid
"""
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "backend"))
_夹具目录 = _ROOT / "fixtures"


def _起库():
    临时 = tempfile.mkdtemp(prefix="haoshu勾稽-")
    os.environ["数据目录"] = 临时
    os.environ["夹具目录"] = str(_夹具目录)
    # 清掉可能已存在的 app 模块缓存，确保 db 路径按新环境变量重算
    for m in list(sys.modules):
        if m == "app" or m.startswith("app."):
            del sys.modules[m]
    from app import seed
    from app.db import 连接
    seed.初始化库()
    return 连接, 临时


def _载夹具(名: str) -> dict:
    return json.loads((_夹具目录 / f"{名}.json").read_text(encoding="utf-8"))


class 金库三方计数测试(unittest.TestCase):
    def setUp(self):
        # 每用例独立临时库：脏链/分叉夹具必须在干净库上造，避免同键跳过污染计数
        连接, self.数据目录 = _起库()
        self.连接厂 = staticmethod(连接)
        from app.services import 装载, 批次勾稽, 贯验
        self.装载 = 装载
        self.勾稽 = 批次勾稽
        self.贯验 = 贯验

    def _数(self, 批号: str) -> dict:
        from app.seed import 枢纽表, 链接表, 卫星表
        conn = self.连接厂()
        try:
            def 数(组):
                return sum(
                    conn.execute(
                        f"SELECT COUNT(*) AS c FROM {t} WHERE 批号=?", (批号,)
                    ).fetchone()["c"]
                    for t in 组
                )
            return {"枢纽": 数(枢纽表), "链接": 数(链接表), "卫星": 数(卫星表)}
        finally:
            conn.close()

    def test_00_建账与迁移(self):
        """老库（无批号列/无状态列/无勾稽表）幂等迁移，历史批补占位不冒充对账。"""
        from app import seed
        老库 = Path(self.数据目录) / "legacy.db"
        c = sqlite3.connect(老库)
        c.executescript(
            """
            CREATE TABLE 装载批次(编号 TEXT PRIMARY KEY, 装载日 TEXT, 时刻 TEXT, 报告 TEXT);
            CREATE TABLE 枢纽_号卡(哈希键 TEXT PRIMARY KEY, 业务键 TEXT, 装载日 TEXT);
            INSERT INTO 装载批次 VALUES ('老批-1','2026-11-20','t','{}');
            INSERT INTO 枢纽_号卡 VALUES ('h1','卡-老','2026-11-20');
            """
        )
        c.commit()
        c.close()
        # 把库连接临时指向老库后跑初始化迁移
        orig = seed.连接
        def 老连接():
            cc = sqlite3.connect(老库)
            cc.row_factory = sqlite3.Row
            cc.execute("PRAGMA foreign_keys = ON")
            return cc
        seed.连接 = 老连接
        try:
            seed.初始化库()
            conn = 老连接()
            try:
                列 = {r["name"] for r in conn.execute("PRAGMA table_info(枢纽_号卡)")}
                self.assertIn("批号", 列)
                列 = {r["name"] for r in conn.execute("PRAGMA table_info(装载批次)")}
                self.assertIn("状态", 列)
                self.assertEqual(
                    conn.execute(
                        "SELECT 状态 FROM 装载批次 WHERE 编号='老批-1'"
                    ).fetchone()["状态"],
                    "成功",
                )
                g = self.勾稽.取批(conn, "老批-1")
                self.assertIsNotNone(g)
                self.assertTrue(g["口径"].startswith("迁移前"))
                self.assertEqual(
                    conn.execute(
                        "SELECT COUNT(*) AS c FROM sqlite_master "
                        "WHERE type='trigger' AND name LIKE '禁%'"
                    ).fetchone()["c"],
                    27,  # 9 表各 3 个（插/删/改）
                )
            finally:
                conn.close()
        finally:
            seed.连接 = orig

    def test_01_正常批通过(self):
        体 = _载夹具("号卡套餐样例")
        r = self.装载.装一批(体)
        self.assertEqual(r["结果"], "成功", r)
        g = r["勾稽"]
        self.assertTrue(g["valid"])
        self.assertEqual((g["回执枢纽"], g["回执链接"], g["回执卫星"]), (6, 4, 8))
        self.assertEqual((g["库增枢纽"], g["库增链接"], g["库增卫星"]), (6, 4, 8))
        self.assertEqual(g["不可解释父子"], [])
        self.assertEqual(self._数("批-样例-01"), {"枢纽": 6, "链接": 4, "卫星": 8})
        # 批成功口径与勾稽同生死：装载批次落成功
        conn = self.连接厂()
        try:
            self.assertEqual(
                conn.execute(
                    "SELECT 状态 FROM 装载批次 WHERE 编号='批-样例-01'"
                ).fetchone()["状态"],
                "成功",
            )
        finally:
            conn.close()

        # 第二批：只开一颗卫星新版，父为装载前已有，仍须可解释并通过
        r2 = self.装载.装一批(_载夹具("套餐升档样例"))
        self.assertEqual(r2["结果"], "成功", r2)
        self.assertTrue(r2["勾稽"]["valid"])
        self.assertEqual(r2["勾稽"]["库增卫星"], 1)
        self.assertEqual(r2["勾稽"]["库增枢纽"], 0)
        self.assertEqual(self._数("批-样例-02"), {"枢纽": 0, "链接": 0, "卫星": 1})

    def test_02_脏链_计数相等但父不可解_乙必须失败(self):
        体 = _载夹具("号卡套餐样例")
        体["批次编号"] = "批-脏链-01"

        def 脏链旁路(conn, 批号):
            # 库增计数完全不动，只污染父子关系：
            # 把一条保留链接的两端哈希 UPDATE 成库里不存在的幽灵枢纽哈希。
            # 行仍带本批标签，链接三计数不变，但两端父在「本批新枢纽 ∪ 装载前已有」均不可解。
            目标 = conn.execute(
                "SELECT 哈希键 FROM 链接_号卡套餐 WHERE 批号=? "
                "AND 号卡哈希=(SELECT 哈希键 FROM 枢纽_号卡 WHERE 业务键='卡-13800010002')",
                (批号,),
            ).fetchone()
            self.assertIsNotNone(目标, "干净库上该批应有一条可污染链接")
            # 该链挂着同批订购卫星，先摘掉并把回执卫星调平，避免卫星父链次生不可解干扰主断言
            删星 = conn.execute(
                "DELETE FROM 卫星_订购属性 WHERE 批号=? AND 链接哈希=?",
                (批号, 目标["哈希键"]),
            )
            self.assertEqual(删星.rowcount, 1, "干净库上该订购链应有一颗同批卫星")
            conn.execute(
                "UPDATE 链接_号卡套餐 SET 号卡哈希='幽灵号卡hash000001', "
                "套餐哈希='幽灵套餐hash000001' WHERE 哈希键=?",
                (目标["哈希键"],),
            )
            # 回执同步调平：库增链接不变、卫星 -1，回执照同一数字记账
            return {"卫星": -1}

        r = self.装载.装一批(体, 旁路写=脏链旁路)
        g = r["勾稽"]
        # 失败闭：回执不得维持成功
        self.assertEqual(r["结果"], "失败")
        self.assertFalse(g["valid"])
        # 三计数确实相等——这正是两口径必须可区分的点
        self.assertTrue(g["计数相等"], "脏链夹具必须保持三计数相等")
        self.assertEqual(g["库增链接"], g["回执链接"])
        # 口径甲（仅计数相等）在此会放行；口径乙凭不可解释父子否决
        self.assertTrue(
            self.勾稽.仅计数相等(
                {"枢纽": g["回执枢纽"], "链接": g["回执链接"], "卫星": g["回执卫星"]},
                {"枢纽": g["库增枢纽"], "链接": g["库增链接"], "卫星": g["库增卫星"]},
            )
        )
        self.assertGreaterEqual(len(g["不可解释父子"]), 2)
        self.assertTrue(any("链接" in x and "不可解" in x for x in g["不可解释父子"]))

        # 数据写入整体回滚：库内无该批任何标签行，装载批次无成功口径
        self.assertEqual(self._数("批-脏链-01"), {"枢纽": 0, "链接": 0, "卫星": 0})
        conn = self.连接厂()
        try:
            self.assertIsNone(
                conn.execute(
                    "SELECT 1 FROM 装载批次 WHERE 编号='批-脏链-01'"
                ).fetchone()
            )
            持久 = self.勾稽.取批(conn, "批-脏链-01")
            self.assertIsNotNone(持久)
            self.assertFalse(持久["valid"])
            self.assertGreaterEqual(len(持久["不可解释父子"]), 2)
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) AS c FROM 隔离匣 WHERE 原因 LIKE '三方对账失败%'"
                ).fetchone()["c"],
                1,
            )
        finally:
            conn.close()

        # 同批号清理后允许重提，成功后勾稽裁决翻为 valid（无成功口径残留阻塞）
        r2 = self.装载.装一批(
            {**_载夹具("号卡套餐样例"), "批次编号": "批-脏链-01"}
        )
        self.assertEqual(r2["结果"], "成功", r2)
        self.assertTrue(r2["勾稽"]["valid"])

    def test_03_并发旁路不得分叉(self):
        # 先装两个正常批打底（样例01 + 升档02），制造封账批与已关版旧卫星
        self.assertEqual(self.装载.装一批(_载夹具("号卡套餐样例"))["结果"], "成功")
        self.assertEqual(self.装载.装一批(_载夹具("套餐升档样例"))["结果"], "成功")

        # (a) 对账窗口互斥：两连接同时 BEGIN IMMEDIATE，后者必须等/拒，旁路无法插进窗口
        a = self.连接厂()
        b = self.连接厂()
        b.execute("PRAGMA busy_timeout=200")
        a.execute("BEGIN IMMEDIATE")
        with self.assertRaises(sqlite3.OperationalError):
            b.execute("BEGIN IMMEDIATE")
        a.rollback()
        a.close()
        b.close()

        conn = self.连接厂()
        try:
            # (b) 封账后旁路插/改/删成功批标签行全部被触发器拒绝
            # 注：SQLite 触发器 RAISE(ABORT) 经驱动映射为 IntegrityError
            with self.assertRaises(sqlite3.IntegrityError) as ctx:
                conn.execute(
                    "INSERT INTO 枢纽_号卡(哈希键, 业务键, 装载日, 批号) "
                    "VALUES ('旁路h0000000001','卡-旁路','2026-11-21','批-样例-01')"
                )
            self.assertIn("封账", str(ctx.exception))

            原哈希 = conn.execute(
                "SELECT 哈希键 FROM 枢纽_号卡 WHERE 批号='批-样例-01' LIMIT 1"
            ).fetchone()["哈希键"]
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "UPDATE 枢纽_号卡 SET 装载日='2026-11-22' WHERE 哈希键=?",
                    (原哈希,),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("DELETE FROM 枢纽_号卡 WHERE 哈希键=?", (原哈希,))

            # 卫星改属性/父键等旁路拒绝；唯一放行：同身份关旧版 是否当前 1→0
            旧版 = conn.execute(
                "SELECT * FROM 卫星_套餐属性 WHERE 批号='批-样例-01' "
                "AND 套餐哈希=(SELECT 哈希键 FROM 枢纽_套餐 WHERE 业务键='套-畅享59') "
                "AND 是否当前=0"
            ).fetchone()
            self.assertIsNotNone(旧版, "样例02开版后样例01旧版应为非当前")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "UPDATE 卫星_套餐属性 SET 属性='{\"被旁路\":true}' WHERE 编号=?",
                    (旧版["编号"],),
                )
        finally:
            conn.close()

        # 经装载序闸开第三版：被关的是批-样例-02 的当前行（同身份 1→0），触发器放行
        r3 = self.装载.装一批({
            "装载日": "2026-11-23",
            "批次编号": "批-样例-03",
            "枢纽": {}, "链接": {},
            "卫星": {"套餐": [{"业务键": "套-畅享59", "属性": {"月费": 59, "流量吉": 50}}]},
        })
        self.assertEqual(r3["结果"], "成功", r3)
        conn = self.连接厂()
        try:
            关版行 = conn.execute(
                "SELECT * FROM 卫星_套餐属性 WHERE 批号='批-样例-02' AND 是否当前=0"
            ).fetchone()
            self.assertIsNotNone(关版行, "第三批开版应把批-样例-02 当前行同身份关版")
        finally:
            conn.close()
        # 封账批标签行计数不变（关版仅改是否当前，批号标签与增量不受影响）
        self.assertEqual(self._数("批-样例-01"), {"枢纽": 6, "链接": 4, "卫星": 8})

        # (c) 贯验按批重数与勾稽裁决一致：无分叉即通过；全库总数不参与判定
        果 = self.贯验.跑贯验()
        复核 = next(x for x in 果["检项"] if x["名"] == "按批勾稽复核")
        self.assertTrue(复核["通过"], 复核["说明"])

    def test_04_计数分叉_失败闭(self):
        体 = _载夹具("号卡套餐样例")
        体["批次编号"] = "批-分叉-01"

        def 多塞枢纽(conn, 批号):
            # 旁路多塞一颗同批标签枢纽：库增枢纽比回执多 1
            conn.execute(
                "INSERT INTO 枢纽_号卡(哈希键, 业务键, 装载日, 批号) "
                "VALUES ('多塞h0000000001','卡-多塞','2026-11-21',?)",
                (批号,),
            )

        r = self.装载.装一批(体, 旁路写=多塞枢纽)
        self.assertEqual(r["结果"], "失败")
        g = r["勾稽"]
        self.assertFalse(g["valid"])
        self.assertFalse(g["计数相等"])
        self.assertEqual(g["库增枢纽"], g["回执枢纽"] + 1)
        self.assertIn("分叉", g["说明"])
        # 失败闭：整批回滚，不留半截成功
        self.assertEqual(self._数("批-分叉-01"), {"枢纽": 0, "链接": 0, "卫星": 0})

    def test_05_只读贯验接口字段(self):
        self.assertEqual(self.装载.装一批(_载夹具("号卡套餐样例"))["结果"], "成功")
        conn = self.连接厂()
        try:
            列 = self.勾稽.列批(conn)
            self.assertTrue(any(x["批号"] == "批-样例-01" for x in 列))
            g = self.勾稽.取批(conn, "批-样例-01")
            for 字段 in (
                "批号", "回执枢纽", "回执链接", "回执卫星",
                "库增枢纽", "库增链接", "库增卫星",
                "不可解释父子", "valid", "口径", "说明",
            ):
                self.assertIn(字段, g)
            self.assertIsInstance(g["不可解释父子"], list)
            self.assertIsInstance(g["valid"], bool)
            self.assertTrue(g["valid"])
            self.assertIn("乙", g["口径"])
        finally:
            conn.close()

    def test_06_贯验纵深_成功批被篡改必报红(self):
        """封账触发器被显式绕过后，贯验按批重数仍须识破成功批增量篡改。"""
        self.assertEqual(self.装载.装一批(_载夹具("号卡套餐样例"))["结果"], "成功")
        conn = self.连接厂()
        try:
            # 正常态：贯验复核通过
            果 = self.贯验.跑贯验()
            self.assertTrue(next(x for x in 果["检项"] if x["名"] == "按批勾稽复核")["通过"])
            # 模拟高权限旁路：先 DROP 触发器再向成功批塞标签行
            conn.execute("DROP TRIGGER 禁插_枢纽_号卡")
            conn.execute(
                "INSERT INTO 枢纽_号卡(哈希键, 业务键, 装载日, 批号) "
                "VALUES ('篡改h0000000001','卡-篡改','2026-11-21','批-样例-01')"
            )
            conn.commit()
        finally:
            conn.close()

        果 = self.贯验.跑贯验()
        复核 = next(x for x in 果["检项"] if x["名"] == "按批勾稽复核")
        self.assertFalse(复核["通过"], "成功批增量被改，贯验必须报红")
        self.assertIn("批-样例-01", 复核["说明"])
        self.assertIn("增量被改", 复核["说明"])
        self.assertFalse(果["是否通过"], "贯验整体裁决必须为未过")

    def test_07_失败批失败闭_贯验证据正确(self):
        """失败批库内零标签行是设计行为；valid=0 报红但不误报失败闭失效。"""
        体 = _载夹具("号卡套餐样例")
        体["批次编号"] = "批-失败-01"

        def 脏链(conn, 批号):
            目标 = conn.execute(
                "SELECT 哈希键 FROM 链接_号卡套餐 WHERE 批号=? "
                "AND 号卡哈希=(SELECT 哈希键 FROM 枢纽_号卡 WHERE 业务键='卡-13800010002')",
                (批号,),
            ).fetchone()
            conn.execute(
                "DELETE FROM 卫星_订购属性 WHERE 批号=? AND 链接哈希=?",
                (批号, 目标["哈希键"]),
            )
            conn.execute(
                "UPDATE 链接_号卡套餐 SET 号卡哈希='幽灵号卡hash000007', "
                "套餐哈希='幽灵套餐hash000007' WHERE 哈希键=?",
                (目标["哈希键"],),
            )
            return {"卫星": -1}

        r = self.装载.装一批(体, 旁路写=脏链)
        self.assertEqual(r["结果"], "失败")
        self.assertTrue(r["勾稽"]["计数相等"])
        self.assertFalse(r["勾稽"]["valid"])
        self.assertEqual(self._数("批-失败-01"), {"枢纽": 0, "链接": 0, "卫星": 0})

        果 = self.贯验.跑贯验()
        复核 = next(x for x in 果["检项"] if x["名"] == "按批勾稽复核")
        self.assertFalse(复核["通过"])
        self.assertIn("valid=0", 复核["说明"])
        self.assertNotIn("失败闭失效", 复核["说明"], "整批已回滚，零残留是正确行为")


if __name__ == "__main__":
    unittest.main(verbosity=2)
