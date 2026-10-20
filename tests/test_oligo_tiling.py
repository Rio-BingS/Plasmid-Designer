"""合成 oligo 片数恒偶：片数由参数决定，不因取整少一片

错位交替（奇数片正链、偶数片反义链）以偶数片为前提。此前
_equal_step_tiles 按 ceil 步长逐片前进，会少返回一片：2570bp/52 →
51 片，整条设计从「偶数片交替」退化为奇数片。
"""
import random

from core.primer_designer import PrimerDesigner

random.seed(5)


def test_tile_count_matches_request():
    d = PrimerDesigner()
    for seq_len, n in [(141, 12), (2570, 52), (2668, 54), (600, 16), (120, 2)]:
        tiles = d._equal_step_tiles(seq_len, n, 20)
        assert len(tiles) == n, (seq_len, n, len(tiles))
        assert tiles[0][0] == 0
        assert tiles[-1][1] == seq_len
        for prev, nxt in zip(tiles, tiles[1:]):
            assert nxt[0] > prev[0]          # 起点严格递增
            assert nxt[0] < prev[1]          # 相邻片重叠（组装依赖）


def test_synthesis_oligo_count_is_even():
    d = PrimerDesigner()
    for seq_len in (600, 2570, 2668):
        seq = "".join(random.choice("ACGT") for _ in range(seq_len))
        oligos = d.design_synthesis_oligos(seq)
        assert oligos, seq_len
        assert len(oligos) % 2 == 0, (seq_len, len(oligos))
