"""合成 oligo 的重叠标注按 oligo 自身方向写

反义 oligo 取的是反向互补链，它的 5' 端对应右侧重叠、3' 端对应左侧；
此前照抄正链的左右，标注反了。
"""
import random
import re

from core.primer_designer import PrimerDesigner

random.seed(3)


def test_antisense_overlap_labels_follow_oligo_direction():
    seq = "".join(random.choice("ACGT") for _ in range(600))
    oligos = PrimerDesigner().design_synthesis_oligos(
        seq, oligo_length_min=40, oligo_length_max=80, overlap_length=20)
    assert len(oligos) >= 2

    for i, o in enumerate(oligos):
        left = oligos[i - 1].target_end - o.target_start if i else 0
        right = o.target_end - oligos[i + 1].target_start if i + 1 < len(oligos) else 0
        is_sense = "_AS" not in o.name
        exp5, exp3 = (left, right) if is_sense else (right, left)
        m = re.search(r"5'(\d+)bp/3'(\d+)bp", o.notes)
        assert m, o.notes
        assert (int(m.group(1)), int(m.group(2))) == (exp5, exp3), o.name
