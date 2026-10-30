"""限制酶位点扫描 — 全站唯一的限制酶模型

酶参数（识别序列 / 正反链切割偏移 / 突出端）一律取自 Biopython 自带的
Bio.Restriction（REBASE 数据），不再手写酶表：此前三套手写表（本模块、
sequence_analysis.RESTRICTION_ENZYMES、clone_strategy 的回退扫描）曾多次
各自写错（KNOWN_ISSUES 6.1/6.4、BsaHI 等）。扫描器本身保持原有实现
（IUPAC 正则、双链、环状跨原点），输出字典形状不变。

Bio.Restriction 首次导入会构建全部 REBASE 酶类（约 6 MB RSS / 0.1 s），
因此只在第一次真正需要酶参数时才导入，模块导入本身不触发。
"""

import re
from collections.abc import Sequence as _SequenceABC
from functools import lru_cache
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

from core.seq_utils import revcomp as _revcomp

# 默认酶菜单：图谱/位点扫描在未指定酶时扫描的集合。即此前 enzyme_sites
# 手写表的 47 种 + 只存在于 sequence_analysis 旧表的 4 种（AvrII/BbsI/
# PvuII/SbfI），合并为 51 种。显式点名时还可使用 REBASE 中任何一把
# 有商业来源、单一切割的酶（见 get_enzyme）。
DEFAULT_ENZYMES: Tuple[str, ...] = (
    "EcoRI", "BamHI", "HindIII", "NotI", "XhoI", "SalI", "PstI", "SmaI",
    "XbaI", "SpeI", "NheI", "KpnI", "SacI", "SphI", "NcoI", "NdeI", "NruI",
    "MluI", "AflII", "AflIII", "AgeI", "ApaI", "AscI", "AvaI", "BglII",
    "BsaI", "BsmBI", "BspHI", "BstBI", "ClaI", "DraI", "EcoRV", "HpaI",
    "KasI", "MluCI", "NsiI", "PacI", "PmeI", "SacII", "ScaI", "SfoI",
    "SnaBI", "SrfI", "StuI", "SwaI", "XcmI", "BsaHI",
    "AvrII", "BbsI", "PvuII", "SbfI",
)

# IUPAC 模糊码 → 正则字符类
_IUPAC = {
    "A": "A", "C": "C", "G": "G", "T": "T",
    "R": "[AG]", "Y": "[CT]", "S": "[GC]", "W": "[AT]",
    "K": "[GT]", "M": "[AC]", "B": "[CGT]", "D": "[AGT]",
    "H": "[ACT]", "V": "[ACG]", "N": "[ACGT]",
}


def _bases(ch: str) -> set:
    return set(_IUPAC.get(ch, ch).strip("[]"))


def _can_self_overlap(site: str) -> bool:
    """识别序列能否与自身错位重叠（如 ACRYGT 在 ACACGTGT 上的两个实例）"""
    n = len(site)
    return any(
        all(_bases(site[i]) & _bases(site[i + k]) for i in range(n - k))
        for k in range(1, n)
    )


@lru_cache(maxsize=None)
def _site_regex(site: str) -> "re.Pattern":
    body = "".join(_IUPAC.get(ch, ch) for ch in site.upper())
    # 可自重叠的识别序列用前瞻断言包裹以报出全部重叠实例，与 REBASE/
    # Bio.Restriction 的计数口径一致（非重叠 finditer 会漏报）；不可能
    # 重叠的序列用普通匹配，扫描快约 4 倍
    if _can_self_overlap(site.upper()):
        return re.compile("(?=" + body + ")")
    return re.compile(body)


def _usable_restriction(enz) -> bool:
    """Bio.Restriction 酶对象是否满足本应用的选用条件。

    只接受有商业来源、每个位点只切一次（单切割对）、切割位置已知、
    识别序列纯 IUPAC 的酶；两侧双切的酶（BaeI 等）与切点未定的酶不可用。
    get_enzyme 与大小写别名索引共用同一判定，避免两处口径漂移。
    """
    from Bio.Restriction.Restriction import RestrictionType

    if not isinstance(enz, RestrictionType):
        return False
    if not enz.is_comm() or enz.is_unknown() or enz.cut_twice():
        return False
    if enz.fst5 is None or enz.fst3 is None:
        return False
    site = str(enz.site).upper()
    return bool(site) and all(ch in _IUPAC for ch in site)


_lower_alias: Optional[Dict[str, str]] = None


def _lower_alias_map() -> Dict[str, str]:
    """可用酶的 {小写名: 规范名} 索引（懒构建一次，进程内复用）。

    Excel 批量表与手写 API 调用里小写酶名（ndei/bamhi）极常见，精确
    getattr 又大小写敏感——索引把「未知限制酶」的假阴性归一到规范拼写。
    """
    global _lower_alias
    if _lower_alias is None:
        from Bio import Restriction as _R
        from Bio.Restriction.Restriction import RestrictionType

        alias: Dict[str, str] = {}
        for attr in dir(_R):
            enz = getattr(_R, attr, None)
            if isinstance(enz, RestrictionType) and enz.__name__ == attr \
                    and _usable_restriction(enz):
                alias.setdefault(attr.lower(), attr)
        _lower_alias = alias
    return _lower_alias


def canonical_enzyme_name(name: str) -> str:
    """酶名归一到规范拼写（ndei → NdeI）；查不到原样返回。

    精确拼写直接返回（快路径）；小写别名索引只在首次未命中时构建一次。
    查不到时不改写——错误提示保留用户原文更有用。
    """
    if not name or not isinstance(name, str):
        return name
    from Bio import Restriction as _R
    from Bio.Restriction.Restriction import RestrictionType

    if isinstance(getattr(_R, name, None), RestrictionType):
        return name
    return _lower_alias_map().get(name.lower(), name)


@lru_cache(maxsize=1024)  # key 是酶名字符串（含未命中噪声），有界防撑内存
def get_enzyme(name: str) -> Optional[Dict]:
    """按名称从 REBASE 取酶参数；未知/不适用的酶返回 None。

    返回 {name, site, cut: (cut_fwd_offset, cut_rev_offset), overhang}，
    与旧手写表同形：cut 为正/反向链切割点相对识别序列 5' 端的偏移
    （= (fst5, size + fst3)，可落在识别序列之外，如 Type IIS），
    overhang 为 '5prime' / '3prime' / None（平末端）。

    名称大小写不敏感（ndei → NdeI）：Excel 批量表的酶列常是小写/全大写。
    只接受有商业来源、每个位点只切一次（单切割对）、切割位置已知的酶；
    两侧双切的酶（BaeI 等）与切点未定的酶返回 None。
    """
    if not name or not isinstance(name, str):
        return None
    from Bio import Restriction as _R  # 惰性导入：见模块文档
    from Bio.Restriction.Restriction import RestrictionType

    enz = getattr(_R, name, None)
    if not (isinstance(enz, RestrictionType) and enz.__name__ == name):
        # 大小写归一到规范拼写后重试（get_enzyme 返回的 name 用规范拼写）
        canonical = canonical_enzyme_name(name)
        if canonical == name:
            return None
        return get_enzyme(canonical)
    if not _usable_restriction(enz):
        return None
    if enz.is_5overhang():
        overhang = "5prime"
    elif enz.is_3overhang():
        overhang = "3prime"
    else:
        overhang = None
    return {
        "name": name,
        "site": str(enz.site).upper(),
        "cut": (enz.fst5, enz.size + enz.fst3),
        "overhang": overhang,
    }


class _LazyEnzymeTable(_SequenceABC):
    """DEFAULT_ENZYMES 的参数表；首次访问时才从 REBASE 构建。

    兼容旧的模块级 ENZYME_TABLE 列表（可迭代/索引/len），但导入本模块
    不会因此加载 Bio.Restriction。
    """

    def _rows(self) -> List[Dict]:
        return _default_table()

    def __getitem__(self, i):
        return self._rows()[i]

    def __len__(self) -> int:
        return len(self._rows())

    def __iter__(self) -> Iterator[Dict]:
        return iter(self._rows())

    def __repr__(self) -> str:
        return f"ENZYME_TABLE({len(DEFAULT_ENZYMES)} enzymes, REBASE-backed)"


@lru_cache(maxsize=1)
def _default_table() -> List[Dict]:
    rows = []
    for name in DEFAULT_ENZYMES:
        spec = get_enzyme(name)
        if spec is None:  # pragma: no cover - 由 test_default_menu_complete 守护
            raise RuntimeError(f"默认酶 {name} 不在 Bio.Restriction 中")
        rows.append(spec)
    return rows


ENZYME_TABLE = _LazyEnzymeTable()


def is_palindromic(spec: Dict) -> bool:
    return _revcomp(spec["site"]) == spec["site"]


def scan_sites(
    seq: str,
    spec: Dict,
    circular: bool = False,
    palindrome_both_strands: bool = True,
) -> Iterator[Tuple[int, str, int, int]]:
    """单把酶的原始扫描结果：逐个产出 (a, strand, cut_fwd, cut_rev)。

    seq 须已大写、U→T。a 为识别序列左端在正向链的 0-based 位置；
    cut_fwd/cut_rev 为正/反向链「切点前一位」的 1-based 坐标，未取模
    （线性序列上的 Type IIS 切点可能落在序列之外，由调用方决定如何处理）。
    palindrome_both_strands=False 时回文识别序列只按正链报一次。
    """
    seq_len = len(seq)
    if seq_len == 0:
        return
    site = spec["site"]
    site_len = len(site)
    c1, c2 = spec["cut"]
    scan_seq = seq
    if circular:
        ext = site_len - 1
        # 极短环：扩展可能超过一圈，按需重复后截取
        scan_seq = seq + (seq * (ext // seq_len + 1))[:ext]
    patterns = [("+", _site_regex(site))]
    if palindrome_both_strands or not is_palindromic(spec):
        # 反向匹配 revcomp(site)，同样直接落在正向链坐标系上
        patterns.append(("-", _site_regex(_revcomp(site))))
    for strand, pattern in patterns:
        for m in pattern.finditer(scan_seq):
            a = m.start()
            if a >= seq_len:
                break  # 起点已绕回原序列：由原位置的匹配代表
            if strand == "+":
                # 正向链切在识别序列第 c1 位之后，反向链第 c2 位之后
                yield a, strand, a + c1, a + c2 + 1
            else:
                # 反向结合位点：位点沿反向阅读，切割坐标镜像
                yield a, strand, a + site_len - c2, a + site_len - c1 + 1


def find_enzyme_sites(
    sequence: str,
    enzymes: Optional[List[str]] = None,
    circular: bool = False,
) -> List[Dict]:
    """扫描序列，返回全部酶切位点（1-based 位置，含反向链识别）。

    enzymes 为空时扫描 DEFAULT_ENZYMES；点名的未知酶静默跳过。

    返回条目: {name, position, strand, cut_fwd, cut_rev, overhang, recognition}
    position 为识别序列 5' 端在正向链上的 1-based 坐标；
    cut_fwd/cut_rev 为正向/反向链切割点（1-based，已对序列长度取模）。
    回文位点按正反链各报一条（图谱/注释依赖此口径）。

    circular=True（质粒/载体）时额外扫描跨越原点的位点：在序列末尾接上
    开头 site_len-1 个碱基再扫描，只保留起点落在原序列内的匹配，
    因此不会重复计数。
    """
    seq = sequence.upper().replace("U", "T")
    seq_len = len(seq)
    results: List[Dict] = []
    if seq_len == 0:
        return results

    if enzymes:
        specs = [s for s in (get_enzyme(n) for n in dict.fromkeys(enzymes)) if s]
    else:
        specs = list(ENZYME_TABLE)

    for spec in specs:
        for a, strand, cut_fwd, cut_rev in scan_sites(seq, spec, circular):
            results.append({
                "name": spec["name"],
                "position": a + 1,
                "strand": strand,
                "cut_fwd": ((cut_fwd - 1) % seq_len) + 1,
                "cut_rev": ((cut_rev - 1) % seq_len) + 1,
                "overhang": spec["overhang"],
                "recognition": spec["site"],
            })

    results.sort(key=lambda r: (r["position"], r["name"]))
    return results


def sites_cut_positions(sites: List[Dict]) -> Dict[str, List[int]]:
    """按酶名汇总切割位置（正向链，1-based 去重排序）"""
    out: Dict[str, List[int]] = {}
    for s in sites:
        cuts = out.setdefault(s["name"], [])
        for c in (s["cut_fwd"], s["cut_rev"]):
            if c not in cuts:
                cuts.append(c)
    out = {k: sorted(v) for k, v in out.items()}
    return out


# ==================== 修饰依赖型酶（数据驱动 + 少量人工名单） ====================

_MOD_DEP_MAX_DEFINITE = 3

# 规则抓不到的修饰依赖型商品酶：SibEnzyme 的 9 把 5mC 依赖「MD DNA endonuclease」
# （只切含 5-甲基胞嘧啶的位点、不切未修饰 DNA；E. coli 质粒没有这类 CpG 甲基化），
# 名单见 http://sibenzyme.com/wp-content/uploads/pdf/1md-dna-endonucleases.pdf 。
# Dam 依赖的 DpnI / MalI（GATC 上的 m6A，dam+ 菌株抽提的质粒确实带）不在此列。
_MODIFICATION_DEPENDENT_NAMES = frozenset({
    "AoxI", "BisI", "BlsI", "GlaI", "GluI", "KroI", "MteI", "PcsI", "PkrI",
})


def is_modification_dependent(spec: Optional[Dict]) -> bool:
    """修饰依赖型酶（Type IIM：只切 5mC / 5hmC / m6A 等修饰过的 DNA）。

    规则：识别序列确定碱基（ACGT）≤ 3 个，且两条链切点都在识别序列之外
    （REBASE 把 AbaSI「C」、FspEI「CC」、MspJI「CNNR」、SgeI「CNNG」、
    LpnPI「CCDG」记作在位点外 12–16 nt 处切割的「位点」，实为一小段修饰
    上下文）——Bio.Restriction 没有专门标记，按几何特征识别；另加
    _MODIFICATION_DEPENDENT_NAMES 中规则抓不到的 CpG 甲基化依赖酶。
    对普通质粒图谱没有意义，且位点数爆炸（AbaSI 每个 C 一个位点），
    图谱的 commercial / unique6 / suppliers 集合都排除。"""
    if not spec:
        return False
    if spec.get("name") in _MODIFICATION_DEPENDENT_NAMES:
        return True
    site = spec["site"]
    size = len(site)
    if sum(1 for ch in site if ch in "ACGT") > _MOD_DEP_MAX_DEFINITE:
        return False
    c1, c2 = spec["cut"]
    lo, hi = min(c1, c2), max(c1, c2)
    return lo >= size or hi <= 0


# ==================== Type IIS / Golden Gate（数据驱动） ====================

_TYPE_IIS_MIN_DEFINITE = 4

def is_type_iis(spec: Optional[Dict]) -> bool:
    """Type IIS 判定（get_enzyme 返回的 spec）。

    规则：识别序列非回文（按 IUPAC 反向互补比较），且两条链的切点都在
    识别序列同一侧之外——全部 ≥ 序列末端且至少一个严格在外（下游切，
    如 BsaI GGTCTC(1/5)、MlyI GAGTC(5/5) 平端），或全部 ≤ 序列起点且
    至少一个严格在外（上游切）。切点有一条落在识别序列内部的非回文酶
    （如 BssSI C^ACGAG）不算；两侧双切的酶（BaeI 等 Type IIB）已被
    get_enzyme 排除。识别序列确定碱基（ACGT）少于 4 个的也不算：REBASE
    里这类条目是修饰依赖型酶（Type IIM，MspJI/FspEI/LpnPI/AbaSI，「位点」
    只是一小段甲基化上下文），不是经典 Type IIS。参考脚本的判定只认纯
    ACGT、且拿相对末端的 fst3 与序列长度比较，会把切点口径算错，这里
    不沿用。
    """
    if not spec:
        return False
    site = spec["site"]
    size = len(site)
    if sum(1 for ch in site if ch in "ACGT") < _TYPE_IIS_MIN_DEFINITE:
        return False
    if _revcomp(site) == site:
        return False
    c1, c2 = spec["cut"]
    lo, hi = min(c1, c2), max(c1, c2)
    downstream = lo >= size and hi > size
    upstream = hi <= 0 and lo < 0
    return downstream or upstream


def golden_gate_geometry(name: str) -> Optional[Dict]:
    """可用于 Golden Gate 引物设计的 Type IIS 酶几何参数；不适用返回 None。

    条件：可商购（get_enzyme 认可）、Type IIS、识别序列纯 ACGT（要原样
    拼进引物）、切出 4 nt 5' 突出端。上游切的酶换算成识别序列反向互补
    写法（等价的下游切）。返回 {name, site, spacer, overhang_len}：
    引物 5'-[site][spacer 个 N][4 nt overhang][退火区]-3'。
    """
    spec = get_enzyme(name)
    if spec is None or not is_type_iis(spec):
        return None
    site = spec["site"]
    if any(ch not in "ACGT" for ch in site):
        return None
    size = len(site)
    c1, c2 = spec["cut"]
    if c1 < 0:  # 上游切：镜像到反向互补位点（顶/底链互换）
        site, c1, c2 = _revcomp(site), size - c2, size - c1
    if c2 - c1 != 4:  # 只要 4 nt 5' 突出端
        return None
    return {"name": name, "site": site, "spacer": c1 - size, "overhang_len": 4}


# Golden Gate 首选酶（文献/试剂盒最常用），其余可用酶按名称排在后面
GOLDEN_GATE_PREFERRED: Tuple[str, ...] = ("BsaI", "BsmBI", "BbsI")


@lru_cache(maxsize=1)
def golden_gate_enzymes() -> Tuple[str, ...]:
    """可商购、切出 4 nt 5' 突出端的全部 Type IIS 酶（Golden Gate 候选）。

    BsaI / BsmBI / BbsI 固定排最前（保持既有偏好），其余按名称排序。"""
    from Bio import Restriction as _R  # 惰性导入：见模块文档

    names = sorted({e.__name__ for e in _R.CommOnly}, key=str.lower)
    ok = [n for n in names if golden_gate_geometry(n) is not None]
    head = [n for n in GOLDEN_GATE_PREFERRED if n in ok]
    return tuple(head + [n for n in ok if n not in head])


@lru_cache(maxsize=1)
def type_iis_enzymes() -> Tuple[str, ...]:
    """可商购的全部 Type IIS 酶（不限突出端），按名称排序"""
    from Bio import Restriction as _R

    names = sorted({e.__name__ for e in _R.CommOnly}, key=str.lower)
    return tuple(n for n in names if is_type_iis(get_enzyme(n)))


def backbone_recut_warnings(
    backbone: str, left_cut: int, right_cut: int, enzymes: Sequence[str],
    *, circular: bool = True,
) -> List[str]:
    """双酶切可行性警告：选中的酶在**保留骨架**上还有切点时给出说明。

    真实酶切不认「规划挑中的那一对」——酶在每处识别序列都切割，骨架上其余
    同酶位点（如 pcDNA3.1 的 SphI 选 1/4）会把保留骨架再切成几段。多切点
    酶切在真实实验里有合理场景（知情接受骨架重切、或全部同酶位点都落在待
    切除区间内），所以这里只产出警告文本、不替用户做决定；是否继续由用户
    通过设计请求的 allow_backbone_cuts 知情确认。

    保留骨架 = 被切除区间 (left_cut, right_cut] 之外的部分（right < left
    跨原点）。边界切点本身不算：粘端连接后位点序列在构建体两端复原
    （plan 的 head/tail 补回残留）。
    """
    left, right = left_cut, right_cut
    excised = (lambda c: left < c <= right) if left < right \
        else (lambda c: c > left or c <= right)
    out: List[str] = []
    for enzyme in dict.fromkeys(e for e in enzymes if e):
        cuts = sorted({s["cut_fwd"] for s in find_enzyme_sites(
            backbone, [enzyme], circular=circular)})
        kept = [c for c in cuts if c not in (left, right) and not excised(c)]
        if kept:
            other = next((e for e in enzymes if e and e != enzyme), None)
            out.append(
                f"{enzyme} 在保留骨架上还有 {len(kept)} 个切点"
                f"（{'、'.join(map(str, kept))}）：酶切会把骨架切成 "
                f"{len(kept) + 1} 段，预测的构建体只有在这些片段恰好不参与"
                "连接的前提下才成立。"
                + (f"若非有意利用多切点，可换掉 {enzyme}（保留 {other}），"
                   "改用骨架上只有唯一位点的酶对。" if other else
                   "若非有意利用多切点，请改用骨架上只有唯一位点的酶对。"))
    return out
