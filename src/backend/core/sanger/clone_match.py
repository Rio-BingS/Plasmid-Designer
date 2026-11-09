"""克隆号模式归组（goal v3 W16 自 batch.py 拆分：batch 超 900 行预算）。

信息表带「克隆号」列时的归组口径：一个质粒多个克隆，每行（克隆）独立
成组、独立分析。匹配优先级、图谱两段式部分匹配与歧义不猜原则见
match_clone_files docstring。基础 helper（norm_stem/squash_name/
primer_name_for/_match_ref_name）仍属 batch.py——本模块只承载克隆归组
本体（app 路由经 batch 的 re-export 使用，import 路径不变）。
"""

import re
from typing import Dict, List, Optional, Tuple

from core.sanger.batch import (
    READ_EXTS,
    _match_ref_name,
    norm_stem,
    primer_name_for,
    squash_name,
)


def match_clone_files(rows: List[Dict], files: List[Dict]):
    """克隆号模式归组：一个质粒多个克隆，每行（克隆）独立成组、独立分析。

    files 的每项为 {"ext", "stem", "name"(可选), "path"(可选)}（同 match_files）。

    read 匹配优先级：
    1. 「克隆号-引物」组合精确（含忽略分隔符形式，S99678_M13F-75 也能命中）；
    2. 引物列条目本身精确（兼容引物列直接填完整文件名的旧表）；
    3. 主干包含某期望组合（复制版后缀 -2/_copy 等）；
    4. 主干以某克隆号开头（克隆号后是分隔符或结尾）——信息表漏填引物、
       或一个克隆只有一个文件时兜底。
    同一引物名出现在多个克隆行时，只写引物名的文件无法归属，判为未匹配。

    图谱 → 质粒名称支持两段式部分匹配：全名精确 > 忽略分隔符精确 >
    与名称的某一段一致（"MBYSTC" ↔ "17648 MBYSTC"）> 双向包含（短侧 ≥4 字符，
    防 "MX" 命中 "MX2"）；候选不唯一一律不猜，判为未匹配。同一质粒的不同
    克隆行可混用全名与单段名（'17648' / 'MBYSTC' / '17648 MBYSTC'）——只写
    一段的名称按双向包含找唯一候选图谱共享（候选多于一张维持缺图谱）。
    图谱文件名也可只写一段（'17648.fasta'）：与图谱共享该段的行直接命中；
    只写另一段的行（'MBYSTC'）与之没有共同标识，仅当表内恰有一个以它为
    组成段的质粒名且该名已确定图谱时，按表内别名共享；表中无全名行时
    确实无法建立对应关系，维持缺图谱。

    返回 (groups, unmatched)。groups 顺序 = 信息表行序，每项：
    {"plasmid", "clone", "reference": {"file","how"}|None, "reads": [{"file","how"}]}
    """
    groups: Dict[Tuple[str, str], Dict] = {}
    for r in rows:
        key = (r["name"], r.get("clone", ""))
        if key not in groups:
            groups[key] = {"plasmid": r["name"], "clone": r.get("clone", ""),
                           "reference": None, "reads": [],
                           "scope_text": r.get("scope") or ""}
        elif r.get("scope") and not groups[key].get("scope_text"):
            groups[key]["scope_text"] = r["scope"]
    names = {r["name"] for r in rows if r["name"]}

    # ---- 图谱 → 质粒名称（返回 质粒名, 说明, 匹配强度 0=全名精确 1=忽略分隔符
    #      精确 2=与名称某一段一致 3=双向包含）----
    def _match_ref(f: Dict) -> Tuple[Optional[str], str, int]:
        stem, sq = f["stem"], squash_name(f["stem"])
        levels = (
            ([n for n in names if norm_stem(n) == stem],
             "文件名与质粒名称精确匹配"),
            ([n for n in names if squash_name(n) == sq],
             "文件名与质粒名称精确匹配（忽略分隔符）"),
            ([n for n in names if sq in {squash_name(t) for t in re.split(r"\s+", n.strip()) if t}],
             None),
        )
        cont = []
        for n in names:
            nsq = squash_name(n)
            if min(len(nsq), len(sq)) >= 4 and (nsq in sq or sq in nsq):
                cont.append(n)
        for level, (cands, how) in enumerate(levels):
            if len(cands) == 1:
                return cands[0], (how or f"文件名与质粒名称的组成部分一致（{cands[0]}）"), level
            if len(cands) > 1:
                return None, ("文件名同时匹配多个质粒名称（"
                              + "、".join(sorted(cands))[:80] + "），无法唯一确定"), level
        if len(cont) == 1:
            return cont[0], "文件名包含质粒名称（或反之）", 3
        if len(cont) > 1:
            return None, ("文件名同时匹配多个质粒名称（"
                          + "、".join(sorted(cont))[:80] + "），无法唯一确定"), 3
        return None, "文件名不匹配任何质粒名称（支持全名或两段式名称的任一段）", 3

    # ---- 期望 read 文件名主干：克隆号-引物 组合 + 引物条目本身 ----
    composed: Dict[str, Tuple[str, str, str]] = {}   # 主干 -> (质粒, 克隆, 匹配方式)
    composed_conflicts = set()                       # 同组合对应多行（如克隆号重复于不同质粒）
    bare: Dict[str, List[Tuple[str, str]]] = {}      # 引物条目 -> [(质粒, 克隆)]
    for r in rows:
        plasmid, clone = r["name"], r.get("clone", "")
        for e in r["primers"]:
            ne = norm_stem(e)
            if not ne:
                continue
            if clone:
                ck = f"{norm_stem(clone)}-{ne}"
                if composed.get(ck, (plasmid, clone))[:2] != (plasmid, clone):
                    composed_conflicts.add(ck)
                composed.setdefault(ck, (plasmid, clone, "克隆号+引物组合精确匹配"))
            bare.setdefault(ne, []).append((plasmid, clone))
    bare_unique = {k: v[0] for k, v in bare.items() if len(v) == 1}
    bare_ambiguous = {k for k, v in bare.items() if len(v) > 1}

    sq_pool: Dict[str, List[Tuple[str, str, str]]] = {}
    for k, v in composed.items():
        if k not in composed_conflicts:
            sq_pool.setdefault(squash_name(k), []).append(v)
    for k, (p, c) in bare_unique.items():
        sq_pool.setdefault(squash_name(k), []).append((p, c, "引物列精确匹配"))

    def _assign_read(f: Dict) -> Tuple[Optional[Tuple[str, str]], str]:
        stem, sq = f["stem"], squash_name(f["stem"])
        if stem in composed_conflicts:
            return None, "「克隆号+引物」组合在信息表中对应多行，无法唯一归属"
        hit = composed.get(stem)
        if hit:
            return (hit[0], hit[1]), hit[2]
        b = bare_unique.get(stem)
        if b:
            return b, "引物列精确匹配"
        hits = sq_pool.get(sq)
        if hits and len(hits) == 1:
            return (hits[0][0], hits[0][1]), hits[0][2]
        if hits:
            return None, "文件名同时匹配多个「克隆号+引物」组合，无法唯一归属"
        # 兜底 1：主干包含某「克隆号+引物」组合（复制版/带额外后缀）。
        # 裸引物条目只做精确匹配、不参与包含——含引物名但克隆号未知/不在表中
        # 的文件（如 S99999-M13F-75）不能凭引物名吸收进某个克隆
        cont = [(k, v) for k, v in composed.items()
                if k not in composed_conflicts and len(squash_name(k)) >= 4 and squash_name(k) in sq]
        if cont:
            uniq = {(v[0], v[1]) for _, v in cont}
            if len(uniq) == 1:
                best = max(cont, key=lambda kv: len(kv[0]))[1]
                return (best[0], best[1]), "文件名包含「克隆号+引物」组合"
            return None, "文件名同时匹配多个克隆的引物组合，无法唯一归属"
        if stem in bare_ambiguous:
            return None, "该引物名出现在多个克隆行，无法确定克隆"
        # 兜底 2：主干以某克隆号开头（分隔符或结尾边界），信息表漏填引物时兜底
        for r in rows:
            c = r.get("clone", "")
            nc = norm_stem(c)
            if len(nc) < 2 or not stem.startswith(nc):
                continue
            nxt = stem[len(nc):len(nc) + 1]
            if nxt and nxt not in "-_ .":
                continue
            return (r["name"], c), f"文件名以克隆号 {c} 开头"
        return None, "未在任何克隆行的引物列中，也匹配不到「克隆号+引物」组合"

    unmatched: List[Dict] = []
    ref_by_plasmid: Dict[str, Dict] = {}
    # 同一质粒名可能被多张图谱文件声明：按匹配强度取最精确者；同强度并列时
    # 仅"文件主干一致"（全名/忽略分隔符精确）视为同一图谱的重复副本取其一，
    # 其余（如两段式部分匹配命中不同文件）无法确定采用哪张，全部不猜
    ref_claims: Dict[str, List[Tuple[Dict, str, int]]] = {}
    for f in files:
        if f["ext"] in READ_EXTS:
            key, how = _assign_read(f)
            if key is None:
                unmatched.append({"file": f, "reason": how})
            else:
                groups.setdefault(key, {"plasmid": key[0], "clone": key[1],
                                        "reference": None, "reads": []})
                groups[key]["reads"].append({"file": f, "how": how})
        else:
            plasmid, how, level = _match_ref(f)
            if plasmid is None:
                unmatched.append({"file": f, "reason": how})
            else:
                ref_claims.setdefault(plasmid, []).append((f, how, level))
    for plasmid, claims in ref_claims.items():
        claims.sort(key=lambda c: c[2])
        best_f, best_how, best_level = claims[0]
        tied = [c for c in claims if c[2] == best_level]
        if len(tied) > 1 and best_level >= 2:
            names_list = "、".join(c[0].get("name") or str(c[0]["path"].name) for c in claims)
            for c in claims:
                unmatched.append({"file": c[0],
                                  "reason": f"同时匹配质粒 {plasmid} 的多张候选图谱（{names_list[:80]}），无法唯一确定"})
            continue
        ref_by_plasmid[plasmid] = {"file": best_f, "how": best_how}
        for c in claims[1:]:
            other = c[0].get("name") or str(c[0]["path"].name)
            unmatched.append({"file": c[0],
                              "reason": f"质粒 {plasmid} 已采用图谱 {best_f.get('name') or str(best_f['path'].name)}，本文件未采用"})

    # 名称驱动兜底：表里同一质粒的不同克隆可能分别只写两段式名称的一段
    # （'17648'/'MBYSTC' 与全名 '17648 MBYSTC' 混用）——只写一段的质粒名
    # 在所有图谱文件中找唯一的双向包含候选，命中则共享该图谱；候选多于
    # 一张（如 'AB2C' 同时命中 '123-1 AB2C' 与 '123-2 AB2C'）维持缺图谱不猜
    ref_files = [f for f in files if f["ext"] not in READ_EXTS]
    for (plasmid, _clone) in list(groups):
        if not plasmid or plasmid in ref_by_plasmid:
            continue
        nsq = squash_name(plasmid)
        if len(nsq) < 4:
            continue
        cands = [f for f in ref_files
                 if min(len(nsq), len(squash_name(f["stem"]))) >= 4
                 and (nsq in squash_name(f["stem"]) or squash_name(f["stem"]) in nsq)]
        if len(cands) == 1:
            ref_by_plasmid[plasmid] = {
                "file": cands[0],
                "how": "质粒名称与图谱文件名部分一致（唯一候选，共享全名质粒的图谱）",
            }

    # 表内别名兜底：图谱文件名只含两段式名称的一段时（如图谱叫 '17648'），
    # 只写另一段的行（'MBYSTC'）与图谱没有共同标识——但信息表内若恰有一个
    # 以它为组成段的质粒名且该名已确定图谱，说明两种写法指向同一质粒，共享
    # 其图谱；这样的宿主名称多于一个（'MBYSTC' 同时是 17648/17649 的组成段）
    # 或宿主本身缺图谱时维持不猜。表中完全没有全名行时（图谱 '17648' ↔
    # 表 'MBYSTC'）确实无法建立对应，保持缺图谱
    for (plasmid, _clone) in list(groups):
        if not plasmid or plasmid in ref_by_plasmid:
            continue
        nsq = squash_name(plasmid)
        if len(nsq) < 2:
            continue
        hosts = [n for n in names if n != plasmid and nsq in
                 {squash_name(t) for t in re.split(r"\s+", n.strip()) if t}]
        if len(hosts) != 1:
            continue
        host_ref = ref_by_plasmid.get(hosts[0])
        if host_ref:
            ref_by_plasmid[plasmid] = {
                "file": host_ref["file"],
                "how": f"「{plasmid}」与「{hosts[0]}」为同一质粒的不同写法（表内互为别名），共享其图谱",
            }

    ordered = list(groups.values())
    for g in ordered:
        g["reference"] = ref_by_plasmid.get(g["plasmid"])
        # W7（PL B3）：逐 read 回填信息表引物列条目名——克隆模式下 read
        # 经「克隆号+引物组合」归属，组合中的引物名即该行引物列的唯一
        # 最长后缀命中（歧义不猜，留 None → unverified 口径）
        _row = next((r for r in rows
                     if r["name"] == g["plasmid"]
                     and r.get("clone", "") == g["clone"]), None)
        for rd in g["reads"]:
            rd["primer"] = primer_name_for(rd["file"]["stem"],
                                           [_row] if _row else [])
    return ordered, unmatched
