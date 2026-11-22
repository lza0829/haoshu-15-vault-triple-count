from app.db import 连接
from app.seed import 此刻, 枢纽表, 链接表, 卫星表
from app.services import 批次勾稽


def 跑贯验() -> dict:
    conn = 连接()
    try:
        枢纽数 = sum(
            conn.execute(f"SELECT COUNT(*) AS c FROM {t}").fetchone()["c"]
            for t in 枢纽表
        )
        链接数 = sum(
            conn.execute(f"SELECT COUNT(*) AS c FROM {t}").fetchone()["c"]
            for t in 链接表
        )
        卫星当前数 = sum(
            conn.execute(f"SELECT COUNT(*) AS c FROM {t} WHERE 是否当前=1").fetchone()["c"]
            for t in 卫星表
        )

        缺链 = []
        for r in conn.execute("SELECT * FROM 链接_号卡套餐"):
            if not conn.execute("SELECT 1 FROM 枢纽_号卡 WHERE 哈希键=?", (r["号卡哈希"],)).fetchone():
                缺链.append(f"订购链缺号卡枢纽:{r['哈希键']}")
            if not conn.execute("SELECT 1 FROM 枢纽_套餐 WHERE 哈希键=?", (r["套餐哈希"],)).fetchone():
                缺链.append(f"订购链缺套餐枢纽:{r['哈希键']}")
        for r in conn.execute("SELECT * FROM 链接_号卡客户"):
            if not conn.execute("SELECT 1 FROM 枢纽_号卡 WHERE 哈希键=?", (r["号卡哈希"],)).fetchone():
                缺链.append(f"实名链缺号卡:{r['哈希键']}")
            if not conn.execute("SELECT 1 FROM 枢纽_客户 WHERE 哈希键=?", (r["客户哈希"],)).fetchone():
                缺链.append(f"实名链缺客户:{r['哈希键']}")

        孤儿 = []
        for 表, 列, 父表 in (
            ("卫星_号卡属性", "号卡哈希", "枢纽_号卡"),
            ("卫星_套餐属性", "套餐哈希", "枢纽_套餐"),
            ("卫星_客户属性", "客户哈希", "枢纽_客户"),
            ("卫星_订购属性", "链接哈希", "链接_号卡套餐"),
        ):
            for r in conn.execute(f"SELECT {列} AS p FROM {表} WHERE 是否当前=1"):
                if not conn.execute(f"SELECT 1 FROM {父表} WHERE 哈希键=?", (r["p"],)).fetchone():
                    孤儿.append(f"{表}:{r['p']}")

        双当前 = []
        for 表, 列 in (
            ("卫星_号卡属性", "号卡哈希"),
            ("卫星_套餐属性", "套餐哈希"),
            ("卫星_客户属性", "客户哈希"),
            ("卫星_订购属性", "链接哈希"),
        ):
            for r in conn.execute(
                f"SELECT {列} AS p, COUNT(*) AS c FROM {表} WHERE 是否当前=1 GROUP BY {列} HAVING c>1"
            ):
                双当前.append(f"{表}:{r['p']}")

        # 按批勾稽复核：重数库内每个批号的可追溯增量，与持久勾稽裁决对照；
        # 全库总数不参与本批判定，只信按批号标签重数。
        勾稽分叉 = []
        for g in 批次勾稽.列批(conn, 限=1000):
            if g["口径"].startswith("迁移前"):
                continue
            库增 = {
                "枢纽": sum(
                    conn.execute(
                        f"SELECT COUNT(*) AS c FROM {t} WHERE 批号=?", (g["批号"],)
                    ).fetchone()["c"]
                    for t in 枢纽表
                ),
                "链接": sum(
                    conn.execute(
                        f"SELECT COUNT(*) AS c FROM {t} WHERE 批号=?", (g["批号"],)
                    ).fetchone()["c"]
                    for t in 链接表
                ),
                "卫星": sum(
                    conn.execute(
                        f"SELECT COUNT(*) AS c FROM {t} WHERE 批号=?", (g["批号"],)
                    ).fetchone()["c"]
                    for t in 卫星表
                ),
            }
            重数三元 = (库增["枢纽"], 库增["链接"], 库增["卫星"])
            裁决三元 = (g["库增枢纽"], g["库增链接"], g["库增卫星"])
            if g["valid"]:
                # 成功封账批：重数必须与裁决一致，不一致即封账后增量被旁路篡改
                if 重数三元 != 裁决三元:
                    勾稽分叉.append(
                        f"成功批{g['批号']}增量被改:裁决{裁决三元}≠重数{重数三元}"
                    )
            else:
                # 失败批按失败闭本应整批回滚（重数全 0）；valid=0 本身即否决项，
                # 若库里竟还残留该批标签行，说明失败闭失效，属更严重的分叉。
                勾稽分叉.append(f"批{g['批号']}勾稽valid=0:{g['说明']}")
                if 重数三元 != (0, 0, 0):
                    勾稽分叉.append(
                        f"失败批{g['批号']}失败闭失效:库内残留标签行{重数三元}"
                    )

        检项 = [
            {
                "名": "链接两端可解",
                "通过": len(缺链) == 0,
                "说明": "全部链接两端枢纽可解" if not 缺链 else "；".join(缺链[:6]),
            },
            {
                "名": "卫星无孤儿",
                "通过": len(孤儿) == 0,
                "说明": "当前卫星均挂到合法父键" if not 孤儿 else "；".join(孤儿[:6]),
            },
            {
                "名": "无双当前",
                "通过": len(双当前) == 0,
                "说明": "同父键仅一条当前卫星" if not 双当前 else "；".join(双当前[:6]),
            },
            {
                "名": "按批勾稽复核",
                "通过": len(勾稽分叉) == 0,
                "说明": "各成功批按批重数与勾稽裁决一致，无valid=0批"
                if not 勾稽分叉
                else "；".join(勾稽分叉[:6]),
            },
        ]
        问题 = 缺链 + [f"孤儿卫星:{x}" for x in 孤儿] + [f"双当前:{x}" for x in 双当前] + 勾稽分叉
        通过 = 0 if 问题 else 1
        说明 = "；".join(问题) if 问题 else "枢纽、链接、卫星引用与当前版贯穿互证通过"
        conn.execute(
            "INSERT INTO 金库贯验(时刻, 枢纽数, 链接数, 卫星当前数, 是否通过, 说明) VALUES (?,?,?,?,?,?)",
            (此刻(), 枢纽数, 链接数, 卫星当前数, 通过, 说明),
        )
        conn.commit()
        return {
            "是否通过": bool(通过),
            "枢纽数": 枢纽数,
            "链接数": 链接数,
            "卫星当前数": 卫星当前数,
            "说明": 说明,
            "检项": 检项,
        }
    finally:
        conn.close()
