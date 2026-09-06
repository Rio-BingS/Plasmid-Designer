"""合成 ABIF (.ab1) 测试文件生成器

按真实 ABIF v1.0 布局构造（tag 4 字节 + number 组成 key，如 'PBAS'+2='PBAS2'；
PCON2 质量值按 type 2 char 存储；DATA9-12 四通道 trace）。用于单测与冒烟验证，
与 Biopython SeqIO('abi') 及内置解析器交叉验证。
"""

import struct


def make_ab1(bases, quals, traces=None):
    """bases: 碱基字符串; quals: phred 质量列表; traces: 4 个通道强度列表

    默认 trace 模拟真实通道：碱基对应主通道 100、其余通道 4（本底）。
    等强度通道会让峰级证据把每个位点都判成 50/50 混合，失真。
    """
    if traces is None:
        traces = [[100 if b == ch else 4 for b in bases] for ch in "ATGC"]
    entries = [
        (b"PBAS", 2, 2, 1, len(bases), bases.encode()),
        (b"PCON", 2, 2, 1, len(quals), bytes(quals)),
        (b"PLOC", 2, 5, 4, len(bases), struct.pack(f">{len(bases)}I", *range(1, len(bases) + 1))),
    ]
    for i, tr in enumerate(traces):
        entries.append((b"DATA", 9 + i, 5, 4, len(tr), struct.pack(f">{len(tr)}i", *tr)))

    dir_size = 28 * len(entries)
    cur = 128 + dir_size
    dir_entries = b""
    blobs = b""
    for tag, num, etype, esize, count, payload in entries:
        dir_entries += struct.pack(">4sIHHIIII", tag, num, etype, esize, count, len(payload), cur, 0)
        blobs += payload
        cur += len(payload)
    header = (
        b"ABIF" + struct.pack(">H4sI2H3I", 100, b"ABIF", 0, 0, 28, len(entries), dir_size, 128)
    ).ljust(128, b"\0")
    return header + dir_entries + blobs
