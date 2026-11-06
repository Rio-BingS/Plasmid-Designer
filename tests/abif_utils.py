"""合成 ABIF (.ab1) 测试文件生成器

按真实 ABIF v1.0 布局构造（tag 4 字节 + number 组成 key，如 'PBAS'+2='PBAS2'；
PCON2 质量值按 type 2 char 存储；DATA9-12 四通道 trace）。用于单测与冒烟验证，
与 Biopython SeqIO('abi') 及内置解析器交叉验证。

make_trace_ab1（goal_sanger_full_fix_v2 T0.3）：高斯峰合成器——每碱基 spacing
个采样点、峰幅抖动、本底噪声、指定位点次级峰（混合/杂合形态）、N 调用。
FWO_1 默认 "GATC"（真实仪器惯例，与 make_ab1 的合成摆放 'ATGC' 刻意不同，
便于测试通道自动检测）。
"""

import math
import random
import struct


def _pack_entries(entries):
    """ABIF 目录项 + 数据区打包（make_ab1 / make_trace_ab1 共用）"""
    dir_size = 28 * len(entries)
    cur = 128 + dir_size
    dir_entries = b""
    blobs = b""
    for tag, num, etype, esize, count, payload in entries:
        if len(payload) <= 4:
            # ABIF 规范：≤4 字节的数据内联在目录项 dataOffset 字段（真实
            # 文件的 FWO_1 即如此存储），Biopython 与内置解析器均按此读取
            dir_entries += struct.pack(">4sIHHII4sI", tag, num, etype, esize,
                                       count, len(payload), payload.ljust(4, b"\0"), 0)
            continue
        dir_entries += struct.pack(">4sIHHIIII", tag, num, etype, esize, count, len(payload), cur, 0)
        blobs += payload
        cur += len(payload)
    header = (
        b"ABIF" + struct.pack(">H4sI2H3I", 100, b"ABIF", 0, 0, 28, len(entries), dir_size, 128)
    ).ljust(128, b"\0")
    return header + dir_entries + blobs


def make_ab1(bases, quals, traces=None, channel_start=9, channel_order="ATGC",
             samples_per_base=1, fwo=None):
    """bases: 碱基字符串; quals: phred 质量列表; traces: 4 个通道强度列表

    默认 trace 模拟真实通道：碱基对应主通道 100、其余通道 4（本底）。
    等强度通道会让峰级证据把每个位点都判成 50/50 混合，失真。

    channel_start/channel_order：四通道写入 DATA{n}..{n+3} 的起始编号与
    通道→碱基顺序。默认 9/'ATGC'（合成摆放，与真实文件的 GATC 惯例刻意
    不同，便于测试通道自动检测）；真实仪器染料组各异。

    fwo：FWO_1 滤光片轮顺序（4 字符如 "GATC" = DATA9=G/10=A/11=T/12=C），
    写入真实 ab1 均携带的 FWO_1 记录；None 不写。用于通道映射优先级测试。

    samples_per_base：每个碱基的 trace 采样点数（真实 ab1 约 20-30）。
    >1 时 PLOC 按采样坐标生成，默认 trace 展开为三角峰（升-平台-降），
    用于峰图计数等依赖采样密度的测试；传自定义 traces 时其长度需为
    samples_per_base * len(bases)。
    """
    n = len(bases)
    if traces is None:
        raw = [[100 if b == ch else 4 for b in bases] for ch in channel_order]
        if samples_per_base > 1:
            traces = []
            for ch in raw:
                expanded = []
                for v in ch:
                    # 峰顶平台对齐 PLOC（offset 0,1），供峰图计数按峰坐标取窗
                    expanded.extend([v, v, 4, 4][:samples_per_base] if samples_per_base <= 4
                                    else [v] * max(2, samples_per_base - 2) + [4, 4])
                traces.append(expanded)
        else:
            traces = raw
    ploc = [i * samples_per_base + 1 for i in range(n)]
    entries = [
        (b"PBAS", 2, 2, 1, n, bases.encode()),
        (b"PCON", 2, 2, 1, len(quals), bytes(quals)),
        (b"PLOC", 2, 5, 4, n, struct.pack(f">{n}I", *ploc)),
    ]
    if fwo is not None:
        # 真实文件 FWO 记录：4 字节 tag "FWO_"（下划线填充）+ number 1，
        # type 2 char 数组、4 字节（≤4 字节内联进目录项）
        entries.append((b"FWO_", 1, 2, 1, 4, fwo.encode("ascii")))
    for i, tr in enumerate(traces):
        entries.append((b"DATA", channel_start + i, 5, 4, len(tr), struct.pack(f">{len(tr)}i", *tr)))
    return _pack_entries(entries)


def make_trace_ab1(seq, *, spacing=12, amp_jitter=(0.8, 1.2), noise=(0, 3),
                   secondary=None, q=50, n_at=(), ploc_base=0, seed=7,
                   channel_start=9, fwo="GATC", tail_width=None,
                   amp_override=None):
    """高斯峰合成 ab1（真实形态：多采样点、峰幅抖动、本底噪声）

    seq：真实分子序列（决定四通道峰形）；调用碱基 = seq，n_at 中的位置
    （1-based）改调为 N（峰形不变——basecaller 对可分辨峰仍可能报 N）。
    secondary：{pos(1-based): (次级碱基, 次级/主峰幅比)}，在指定位点叠加
    次级通道高斯峰（混合克隆 / 杂合位点形态；次级碱基可与主峰同割据）。
    q：int（全线同 Q）或逐碱基列表。amp_jitter：逐峰幅均匀抖动倍率区间。
    noise：逐采样点均匀本底区间（各通道独立）。
    spacing：每碱基采样点数（真实 ab1 约 10-25）。PLOC = 峰心采样点索引
    （0 基，与真实文件一致——解析端的 −1 口径是已知挂账 T24.1）。
    ploc_base：首峰采样点偏移。seed：随机种子（默认 7，保证用例可复现）。
    fwo：通道→碱基顺序（DATA{start}..{start+3}），默认真实惯例 "GATC"。
    tail_width：(起始位置, 倍数)——该位置（1-based）起峰宽 ×倍数（模拟
    真实 read 尾端峰展宽/分辨率下降而 Q 未必掉；T12 信号驱动可信区的
    场景依赖峰形而非 Q 单独刻画尾端退化）。
    amp_override：{pos(1-based): 倍率}——指定位点的主峰幅乘数（<1 模拟
    弱信号/无独立插入峰，M14 的单 low 票前提）。
    """
    rng = random.Random(seed)
    seq = seq.upper()
    n = len(seq)
    sigma = max(2.0, spacing / 4.5)
    centers = [ploc_base + i * spacing for i in range(n)]
    n_samples = centers[-1] + int(3 * sigma) + 2 if n else spacing
    amps = [rng.uniform(*amp_jitter) for _ in range(n)]
    amp_override = amp_override or {}

    channels = list(fwo)
    trace = {ch: [0.0] * n_samples for ch in channels}

    def _sigma_at(i: int) -> float:
        return sigma * tail_width[1] if tail_width and i + 1 >= tail_width[0] else sigma

    def _add_peak(ch, center, height, sg):
        if ch not in trace or height <= 0:
            return
        lo = max(0, center - int(3 * sg) - 1)
        hi = min(n_samples, center + int(3 * sg) + 2)
        for x in range(lo, hi):
            trace[ch][x] += height * math.exp(-0.5 * ((x - center) / sg) ** 2)

    for i, b in enumerate(seq):
        if b in channels:
            _add_peak(b, centers[i], 100.0 * amps[i] * amp_override.get(i + 1, 1.0),
                      _sigma_at(i))
    for pos, sbase, frac in _secondary_items(secondary):
        i = pos - 1
        if 0 <= i < n:
            _add_peak(sbase, centers[i], 100.0 * amps[i] * frac, _sigma_at(i))

    for ch in channels:
        base_line = trace[ch]
        trace[ch] = [int(round(v + rng.uniform(*noise))) for v in base_line]

    calls = list(seq)
    for p in n_at:
        if 1 <= p <= n:
            calls[p - 1] = "N"
    quals = list(q) if isinstance(q, (list, tuple)) else [q] * n
    if len(quals) != n:
        raise ValueError(f"q 长度 {len(quals)} != 序列长 {n}")

    entries = [
        (b"PBAS", 2, 2, 1, n, "".join(calls).encode()),
        (b"PCON", 2, 2, 1, n, bytes(quals)),
        (b"PLOC", 2, 5, 4, n, struct.pack(f">{n}I", *centers)),
        (b"FWO_", 1, 2, 1, 4, fwo.encode("ascii")),
    ]
    for i, ch in enumerate(channels):
        entries.append((b"DATA", channel_start + i, 5, 4, n_samples,
                        struct.pack(f">{n_samples}i", *trace[ch])))
    return _pack_entries(entries)


def _secondary_items(secondary):
    """secondary 归一：{pos: (base, frac)} 或 {pos: base}（frac=1.0 等高）"""
    if not secondary:
        return []
    items = []
    for pos, val in secondary.items():
        if isinstance(val, str):
            items.append((pos, val, 1.0))
        else:
            base, frac = val
            items.append((pos, base, frac))
    return items
