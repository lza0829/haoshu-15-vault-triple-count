import json
import uuid
from typing import Any, Callable

from app.db import 连接
from app.seed import 哈希键, 载荷哈希, 此刻
from app.services import 批次勾稽


class _勾稽失败(Exception):
    def __init__(self, 果: dict):
        super().__init__(果["说明"])
        self.果 = 果


def 装一批(体: dict[str, Any], 旁路写: Callable | None = None) -> dict[str, Any]:
    """顺序：枢纽 → 链接 → 卫星。三方对账与批成功标志同成败，失败闭。

    全程单事务（BEGIN IMMEDIATE 持写锁，杜绝对账窗口内并发旁路）：
    写入（行级批号标签）→ 按批对账（口径乙）→ 同成败封账。
    对账不过：回滚本批数据写入，仅持久 valid=0 的勾稽裁决与隔离匣记录，
    不写装载批次成功口径，回执不得维持成功。

    旁路写：仅供夹具测试注入同批旁路脏写，路由层从不传入。
    """
    装载日 = str(体.get("装载日") or "").strip()
    枢纽 = 体.get("枢纽") or {}
    链接 = 体.get("链接") or {}
    卫星 = 体.get("卫星") or {}
    if not 装载日:
        return {"结果": "拒绝", "说明": "缺装载日"}

    批号 = str(体.get("批次编号") or f"批-{uuid.uuid4().hex[:10]}")
    conn = 连接()
    conn.execute("PRAGMA busy_timeout=10000")
    if 旁路写 is not None:  # 测试专用：夹具需绕过外键造父不可解脏链
        conn.execute("PRAGMA foreign_keys=OFF")
    # 默认隔离级别下显式开立即事务：持写锁直到提交，对账单连接内可重读自己的写入
    try:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute(
            "SELECT 1 FROM 装载批次 WHERE 编号=? AND 状态='成功'", (批号,)
        ).fetchone():
            conn.rollback()
            return {"结果": "拒绝", "说明": "批次编号已存在且已成功封账"}

        # 装载前父键宇宙快照（勾稽可解释性的「装载前已有」半边）
        前快照 = 批次勾稽.装载前快照(conn)
        报 = {"枢纽": 0, "链接": 0, "卫星": 0, "跳过": 0, "明细": []}

        try:
            conn.execute("SAVEPOINT 装载写")

            # 枢纽
            for 类, 表, 前缀 in (
                ("号卡", "枢纽_号卡", "号卡"),
                ("套餐", "枢纽_套餐", "套餐"),
                ("客户", "枢纽_客户", "客户"),
            ):
                for 项 in 枢纽.get(类) or []:
                    业务键 = str(项.get("业务键") or "").strip()
                    if not 业务键:
                        报["跳过"] += 1
                        continue
                    h = 哈希键(前缀, 业务键)
                    旧 = conn.execute(
                        f"SELECT 1 FROM {表} WHERE 业务键=?", (业务键,)
                    ).fetchone()
                    if 旧:
                        报["跳过"] += 1
                        报["明细"].append(
                            {"类": f"枢纽-{类}", "业务键": 业务键, "结果": "已存在"}
                        )
                        continue
                    conn.execute(
                        f"INSERT INTO {表}(哈希键, 业务键, 装载日, 批号) "
                        f"VALUES (?,?,?,?)",
                        (h, 业务键, 装载日, 批号),
                    )
                    报["枢纽"] += 1
                    报["明细"].append(
                        {"类": f"枢纽-{类}", "业务键": 业务键, "哈希键": h, "结果": "新开"}
                    )

            def 取枢纽哈希(表, 业务键):
                r = conn.execute(
                    f"SELECT 哈希键 FROM {表} WHERE 业务键=?", (业务键,)
                ).fetchone()
                return r["哈希键"] if r else None

            # 链接 号卡-套餐
            for 项 in 链接.get("号卡套餐") or []:
                号 = str(项.get("号卡业务键") or "").strip()
                套 = str(项.get("套餐业务键") or "").strip()
                hk卡 = 取枢纽哈希("枢纽_号卡", 号)
                hk套 = 取枢纽哈希("枢纽_套餐", 套)
                if not hk卡 or not hk套:
                    raise ValueError(f"链接号卡套餐缺枢纽: {号}/{套}")
                lh = 哈希键("链订购", hk卡, hk套)
                if conn.execute(
                    "SELECT 1 FROM 链接_号卡套餐 WHERE 哈希键=?", (lh,)
                ).fetchone():
                    报["跳过"] += 1
                    continue
                conn.execute(
                    "INSERT INTO 链接_号卡套餐(哈希键, 号卡哈希, 套餐哈希, 装载日, 批号) "
                    "VALUES (?,?,?,?,?)",
                    (lh, hk卡, hk套, 装载日, 批号),
                )
                报["链接"] += 1

            # 链接 号卡-客户
            for 项 in 链接.get("号卡客户") or []:
                号 = str(项.get("号卡业务键") or "").strip()
                客 = str(项.get("客户业务键") or "").strip()
                hk卡 = 取枢纽哈希("枢纽_号卡", 号)
                hk客 = 取枢纽哈希("枢纽_客户", 客)
                if not hk卡 or not hk客:
                    raise ValueError(f"链接号卡客户缺枢纽: {号}/{客}")
                lh = 哈希键("链实名", hk卡, hk客)
                if conn.execute(
                    "SELECT 1 FROM 链接_号卡客户 WHERE 哈希键=?", (lh,)
                ).fetchone():
                    报["跳过"] += 1
                    continue
                conn.execute(
                    "INSERT INTO 链接_号卡客户(哈希键, 号卡哈希, 客户哈希, 装载日, 批号) "
                    "VALUES (?,?,?,?,?)",
                    (lh, hk卡, hk客, 装载日, 批号),
                )
                报["链接"] += 1

            def 开卫星(表, 父列, 父哈希, 属性: dict):
                ph = 载荷哈希(属性)
                当前 = conn.execute(
                    f"SELECT 编号, 载荷哈希 FROM {表} WHERE {父列}=? AND 是否当前=1",
                    (父哈希,),
                ).fetchone()
                if 当前 and 当前["载荷哈希"] == ph:
                    报["跳过"] += 1
                    return
                if 当前:
                    conn.execute(
                        f"UPDATE {表} SET 是否当前=0 WHERE 编号=?", (当前["编号"],)
                    )
                conn.execute(
                    f"INSERT INTO {表}({父列}, 装载日, 载荷哈希, 属性, 是否当前, 批号) "
                    f"VALUES (?,?,?,?,1,?)",
                    (父哈希, 装载日, ph, json.dumps(属性, ensure_ascii=False), 批号),
                )
                报["卫星"] += 1

            for 项 in 卫星.get("号卡") or []:
                号 = str(项.get("业务键") or "").strip()
                h = 取枢纽哈希("枢纽_号卡", 号)
                if not h:
                    raise ValueError(f"卫星号卡无枢纽: {号}")
                开卫星("卫星_号卡属性", "号卡哈希", h, 项.get("属性") or {})

            for 项 in 卫星.get("套餐") or []:
                套 = str(项.get("业务键") or "").strip()
                h = 取枢纽哈希("枢纽_套餐", 套)
                if not h:
                    raise ValueError(f"卫星套餐无枢纽: {套}")
                开卫星("卫星_套餐属性", "套餐哈希", h, 项.get("属性") or {})

            for 项 in 卫星.get("客户") or []:
                客 = str(项.get("业务键") or "").strip()
                h = 取枢纽哈希("枢纽_客户", 客)
                if not h:
                    raise ValueError(f"卫星客户无枢纽: {客}")
                开卫星("卫星_客户属性", "客户哈希", h, 项.get("属性") or {})

            for 项 in 卫星.get("订购") or []:
                号 = str(项.get("号卡业务键") or "").strip()
                套 = str(项.get("套餐业务键") or "").strip()
                hk卡 = 取枢纽哈希("枢纽_号卡", 号)
                hk套 = 取枢纽哈希("枢纽_套餐", 套)
                if not hk卡 or not hk套:
                    raise ValueError(f"卫星订购无链接端: {号}/{套}")
                lh = 哈希键("链订购", hk卡, hk套)
                if not conn.execute(
                    "SELECT 1 FROM 链接_号卡套餐 WHERE 哈希键=?", (lh,)
                ).fetchone():
                    raise ValueError(f"卫星订购无链接: {号}/{套}")
                开卫星("卫星_订购属性", "链接哈希", lh, 项.get("属性") or {})

            # 夹具注入：对账前的同批旁路脏写；回调可返回回执调平量模拟失真回执
            if 旁路写 is not None:
                调平 = 旁路写(conn, 批号) or {}
                for k in ("枢纽", "链接", "卫星"):
                    报[k] = max(0, 报[k] + int(调平.get(k, 0)))

            # 三方对账（口径乙）：与批成功标志同成败的状态门槛
            果 = 批次勾稽.批对账(conn, 批号, 报, 前快照)
            if not 果["valid"]:
                raise _勾稽失败(果)

            # 成功封账：批次成功口径 + 勾稽裁决同一原子提交
            conn.execute(
                "INSERT INTO 装载批次(编号, 装载日, 时刻, 报告, 状态) "
                "VALUES (?,?,?,?,'成功')",
                (批号, 装载日, 此刻(), json.dumps(报, ensure_ascii=False)),
            )
            批次勾稽.记勾稽(conn, 此刻(), 果)
            conn.commit()
            return {
                "结果": "成功",
                "批次编号": 批号,
                "装载日": 装载日,
                "报告": 报,
                "勾稽": 果,
            }
        except _勾稽失败 as e:
            # 失败闭：回滚本批全部数据写入，不留成功口径与按批增量的分叉
            conn.execute("ROLLBACK TO SAVEPOINT 装载写")
            conn.execute("RELEASE SAVEPOINT 装载写")
            批次勾稽.记勾稽(conn, 此刻(), e.果)
            conn.execute(
                "INSERT INTO 隔离匣(原因, 载荷, 时刻) VALUES (?,?,?)",
                (
                    "三方对账失败:" + e.果["说明"],
                    json.dumps(体, ensure_ascii=False)[:2000],
                    此刻(),
                ),
            )
            conn.commit()
            return {
                "结果": "失败",
                "批次编号": 批号,
                "说明": "三方对账失败，本批已失败闭回滚:" + e.果["说明"],
                "勾稽": e.果,
            }
        except Exception as e:
            conn.execute("ROLLBACK TO SAVEPOINT 装载写")
            conn.execute("RELEASE SAVEPOINT 装载写")
            conn.execute(
                "INSERT INTO 隔离匣(原因, 载荷, 时刻) VALUES (?,?,?)",
                (str(e), json.dumps(体, ensure_ascii=False)[:2000], 此刻()),
            )
            conn.commit()
            return {"结果": "失败", "说明": str(e)}
    finally:
        conn.close()
