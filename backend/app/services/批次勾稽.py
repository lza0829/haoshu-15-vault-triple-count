"""金库三方计数：按批勾稽（口径乙，全局唯一口径出处）。

口径乙（计数相等且批内勾稽可解释）：
  1. 回执枢纽/链接/卫星三计数 == 库内按批号可追溯的本批增量三计数；禁止全库总数冒充本批。
  2. 本批每条新链接两端枢纽，必须能在「本批新枢纽 ∪ 装载前已有枢纽」中解释；
     本批每条新卫星父，必须能在「本批新父 ∪ 装载前已有父」中解释。
  3. 任一不满足 => valid=0。三计数相等但混入父不可解脏链时，valid 仍为 0，
     批不得维持成功（与口径甲「仅三计数相等即 valid」的结论在此可区分）。
"""
import json

from app.seed import 快照表, 卫星组, 枢纽表, 链接表, 卫星表

口径名 = "乙：三计数相等且批内勾稽可解释"


def 装载前快照(conn) -> dict[str, set[str]]:
    """事务开始时拍下的父键宇宙：装载前已有父。"""
    return {
        表: {r["哈希键"] for r in conn.execute(f"SELECT 哈希键 FROM {表}")}
        for 表 in 快照表
    }


def 仅计数相等(回执: dict, 库增: dict) -> bool:
    """口径甲的判定，仅保留用于夹具可区分性自证，不作成功闸依据。"""
    return (
        回执["枢纽"] == 库增["枢纽"]
        and 回执["链接"] == 库增["链接"]
        and 回执["卫星"] == 库增["卫星"]
    )


def 批对账(conn, 批号: str, 回执: dict, 前快照: dict[str, set[str]]) -> dict:
    """对同一事务连接内的本批写入做只读对账（不落盘）。"""
    库增 = {
        "枢纽": sum(
            conn.execute(
                f"SELECT COUNT(*) AS c FROM {t} WHERE 批号=?", (批号,)
            ).fetchone()["c"]
            for t in 枢纽表
        ),
        "链接": sum(
            conn.execute(
                f"SELECT COUNT(*) AS c FROM {t} WHERE 批号=?", (批号,)
            ).fetchone()["c"]
            for t in 链接表
        ),
        "卫星": sum(
            conn.execute(
                f"SELECT COUNT(*) AS c FROM {t} WHERE 批号=?", (批号,)
            ).fetchone()["c"]
            for t in 卫星表
        ),
    }

    不可解释: list[str] = []

    # 可解释宇宙 = 本批新父（标签行）∪ 装载前已有父（事务开始快照）
    宇宙 = {
        表: set(前快照.get(表, set()))
        | {
            r["哈希键"]
            for r in conn.execute(f"SELECT 哈希键 FROM {表} WHERE 批号=?", (批号,))
        }
        for 表 in 快照表
    }

    # 链接两端必须可解释
    链接端列 = {
        "链接_号卡套餐": (("号卡哈希", "枢纽_号卡"), ("套餐哈希", "枢纽_套餐")),
        "链接_号卡客户": (("号卡哈希", "枢纽_号卡"), ("客户哈希", "枢纽_客户")),
    }
    for 表, 端组 in 链接端列.items():
        for r in conn.execute(f"SELECT * FROM {表} WHERE 批号=?", (批号,)):
            for 端列, 父表 in 端组:
                if r[端列] not in 宇宙[父表]:
                    不可解释.append(f"链接:{表}:{r['哈希键']}:端{端列}={r[端列]}不可解")

    # 卫星父必须可解释
    for 表, 父列, 父表 in 卫星组:
        for r in conn.execute(
            f"SELECT 编号, {父列} AS p FROM {表} WHERE 批号=?", (批号,)
        ):
            if r["p"] not in 宇宙[父表]:
                不可解释.append(f"卫星:{表}:编号{r['编号']}:父{父列}={r['p']}不可解")

    计数相等 = 仅计数相等(回执, 库增)
    # 口径乙：计数相等且无不可解释父子，缺一不可
    valid = 计数相等 and not 不可解释

    说明: list[str] = []
    if not 计数相等:
        说明.append(
            f"三计数分叉:回执({回执['枢纽']}/{回执['链接']}/{回执['卫星']})"
            f"≠库增({库增['枢纽']}/{库增['链接']}/{库增['卫星']})"
        )
    if 不可解释:
        说明.append(f"批内父子不可解释{len(不可解释)}处:" + "；".join(不可解释[:6]))
    if valid:
        说明.append("三计数相等且批内勾稽可解释")

    return {
        "批号": 批号,
        "口径": 口径名,
        "回执枢纽": 回执["枢纽"],
        "回执链接": 回执["链接"],
        "回执卫星": 回执["卫星"],
        "库增枢纽": 库增["枢纽"],
        "库增链接": 库增["链接"],
        "库增卫星": 库增["卫星"],
        "计数相等": 计数相等,
        "不可解释父子": 不可解释,
        "valid": valid,
        "说明": "；".join(说明),
    }


def 记勾稽(conn, 时刻: str, 果: dict) -> None:
    """把对账裁决持久进批次勾稽表（调用方负责提交）。"""
    conn.execute(
        "INSERT INTO 批次勾稽"
        "(批号, 时刻, 回执枢纽, 回执链接, 回执卫星, 库增枢纽, 库增链接, 库增卫星, "
        " 不可解释父子, valid, 口径, 说明) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(批号) DO UPDATE SET "
        "时刻=excluded.时刻, 回执枢纽=excluded.回执枢纽, 回执链接=excluded.回执链接, "
        "回执卫星=excluded.回执卫星, 库增枢纽=excluded.库增枢纽, 库增链接=excluded.库增链接, "
        "库增卫星=excluded.库增卫星, 不可解释父子=excluded.不可解释父子, valid=excluded.valid, "
        "口径=excluded.口径, 说明=excluded.说明",
        (
            果["批号"],
            时刻,
            果["回执枢纽"], 果["回执链接"], 果["回执卫星"],
            果["库增枢纽"], 果["库增链接"], 果["库增卫星"],
            json.dumps(果["不可解释父子"], ensure_ascii=False),
            1 if 果["valid"] else 0,
            果["口径"],
            果["说明"],
        ),
    )


def 取批(conn, 批号: str) -> dict | None:
    r = conn.execute("SELECT * FROM 批次勾稽 WHERE 批号=?", (批号,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["不可解释父子"] = json.loads(d["不可解释父子"])
    d["valid"] = bool(d["valid"])
    return d


def 列批(conn, 限: int = 50) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM 批次勾稽 ORDER BY 时刻 DESC, 批号 DESC LIMIT ?", (限,)
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["不可解释父子"] = json.loads(d["不可解释父子"])
        d["valid"] = bool(d["valid"])
        out.append(d)
    return out
