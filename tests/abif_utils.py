"""合成 ABIF (.ab1) 测试文件生成器

按真实 ABIF v1.0 布局构造（tag 4 字节 + number 组成 key，如 'PBAS'+2='PBAS2'；
PCON2 质量值按 type 2 char 存储；DATA9-12 四通道 trace）。用于单测与冒烟验证，
与 Biopython SeqIO('abi') 及内置解析器交叉验证。
"""

import struct


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
