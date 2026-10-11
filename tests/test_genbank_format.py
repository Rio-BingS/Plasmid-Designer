"""GenBank LOCUS 行共享格式化（core/genbank_format.py）测试

每条 LOCUS 行都拼成完整 GenBank 记录，交给 Biopython 以「告警即错误」
解析回读：列位置一旦偏离 NCBI 规范，Biopython 会发
BiopythonParserWarning，这里直接失败。
"""

import io
import warnings
from datetime import datetime

import pytest
from Bio import BiopythonParserWarning, BiopythonWarning, SeqIO
from Bio.Seq import Seq
from Bio.SeqRecord import SeqRecord

from core.genbank_format import format_locus_line

DATE = "01-JAN-2026"


def _record_text(locus_line: str, length: int) -> str:
    seq = ("ACGT" * (length // 4 + 1))[:length]
    origin = []
    for i in range(0, len(seq), 60):
        chunk = seq[i:i + 60]
        groups = " ".join(chunk[j:j + 10] for j in range(0, len(chunk), 10))
        origin.append(f"{i + 1:>9} {groups}")
    return (locus_line + "\n"
            "DEFINITION  locus line test.\n"
            "FEATURES             Location/Qualifiers\n"
            f"     source          1..{length}\n"
            "ORIGIN\n" + "\n".join(origin) + "\n//\n")


def _round_trip(locus_line: str, length: int):
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        return SeqIO.read(io.StringIO(_record_text(locus_line, length)), "genbank")


@pytest.mark.parametrize("topology", ["linear", "circular"])
def test_short_name_standard_columns(topology):
    line = format_locus_line("pUC19", 2686, topology=topology, date=DATE)
    # NCBI 列规范（1 起算）：名称第 13 列起，长度第 40 列止，bp 42-43，
    # 分子类型 48 起，拓扑 56-63，分类 65-67，日期 69-79
    assert len(line) == 79
    assert line[12:17] == "pUC19"
    assert line[29:40] == "2686".rjust(11)
    assert line[41:43] == "bp"
    assert line[47:50] == "DNA"
    assert line[55:63] == topology.ljust(8)
    assert line[64:67] == "SYN"
    assert line[68:79] == DATE

    rec = _round_trip(line, 2686)
    assert rec.name == "pUC19"
    assert len(rec) == 2686
    assert rec.annotations["topology"] == topology
    assert rec.annotations["data_file_division"] == "SYN"
    assert rec.annotations["date"] == DATE


@pytest.mark.parametrize("topology", ["linear", "circular"])
def test_matches_biopython_writer(topology):
    """与 Biopython 自带写出器的 LOCUS 行逐字一致"""
    rec = SeqRecord(Seq("ACGT" * 30), id="TopoRef", name="TopoRef",
                    annotations={"molecule_type": "DNA", "topology": topology,
                                 "data_file_division": "SYN", "date": DATE})
    expected = rec.format("genbank").splitlines()[0]
    assert format_locus_line("TopoRef", 120, topology=topology, date=DATE) == expected


def test_long_name_truncated_by_default():
    name = "a_very_long_construct_name_v2"
    line = format_locus_line(name, 123456, topology="circular", date=DATE)
    assert len(line) == 79
    rec = _round_trip(line, 123456)
    assert rec.name == name[:16]
    assert len(rec) == 123456


def test_long_name_kept_lengthens_line():
    """保留全名时整行加长，名称与长度之间至少一个空格"""
    name = "a_very_long_construct_name_v2"  # 29 字符，放不进 13-28 列
    line = format_locus_line(name, 5428, topology="linear", date=DATE,
                             max_name_len=None)
    assert f"{name} 5428 bp" in line
    rec = _round_trip(line, 5428)
    assert rec.name == name
    assert len(rec) == 5428
    assert rec.annotations["topology"] == "linear"

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", BiopythonWarning)  # 写出器的加长提示
        expected = SeqRecord(Seq("A" * 5428), id=name, name=name,
                             annotations={"molecule_type": "DNA", "topology": "linear",
                                          "data_file_division": "SYN", "date": DATE}
                             ).format("genbank").splitlines()[0]
    assert line == expected


def test_medium_name_shifts_but_stays_in_standard_width():
    """17-23 字符名称 + 短长度仍可放进标准 28 列，不加长"""
    name = "pcDNA3.1_EGFP_v2_ok"  # 19 字符
    line = format_locus_line(name, 120, date=DATE, max_name_len=None)
    assert len(line) == 79
    assert line[12:12 + len(name)] == name
    assert _round_trip(line, 120).name == name


def test_odd_characters_sanitized():
    line = format_locus_line(" pcDNA3.1(+) with\tGFP ", 5428, topology="circular",
                             date=DATE)
    rec = _round_trip(line, 5428)
    assert rec.name == "pcDNA3.1(+)_with"
    assert " " not in rec.name


def test_unicode_and_empty_names():
    rec = _round_trip(format_locus_line("样品1-consensus", 300, date=DATE), 300)
    assert rec.name == "样品1-consensus"
    rec = _round_trip(format_locus_line("", 60, date=DATE), 60)
    assert rec.name == "unnamed"


def test_date_defaults_and_datetime():
    line = format_locus_line("x", 10, date=datetime(2026, 3, 5))
    assert line.endswith("05-MAR-2026")
    today = datetime.now().strftime("%d-%b-%Y").upper()
    assert format_locus_line("x", 10).endswith(today)


@pytest.mark.parametrize("kwargs", [
    {"topology": "ring"},
    {"division": "SYNTH"},
    {"mol_type": "genomic DNA"},
])
def test_invalid_arguments_rejected(kwargs):
    with pytest.raises(ValueError):
        format_locus_line("x", 10, **kwargs)


def test_parser_warning_is_error_in_suite():
    """pytest 配置把 BiopythonParserWarning 升级为错误，防回归"""
    with pytest.raises(BiopythonParserWarning):
        warnings.warn("malformed", BiopythonParserWarning)


# ==================== 各导出入口回读 ====================

@pytest.mark.parametrize("circular", [True, False])
def test_export_formats_genbank_round_trips(circular):
    from core.export_formats import ExportData, GenBankExporter

    seq = "ATGC" * 50
    text = GenBankExporter.export(ExportData(
        name="my plasmid-v1 extra long", sequence=seq, features=[],
        is_circular=circular, created_at=datetime(2026, 1, 1)))
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        rec = SeqIO.read(io.StringIO(text), "genbank")
    assert rec.name == "my_plasmid_v1_ex"
    assert len(rec) == len(seq)
    assert rec.annotations["topology"] == ("circular" if circular else "linear")
    assert rec.annotations["date"] == DATE


def test_output_generator_genbank_round_trips():
    from core.output_generator import GenBankGenerator, PlasmidDesign

    seq = "ATGC" * 40
    design = PlasmidDesign(
        design_id="d1", design_name="pET28a GFP construct", insert_sequence="ATG",
        insert_name="GFP", vector_id="pET-28a", vector_name="pET-28a",
        final_sequence=seq, primers=[], cloning_method="gibson",
        design_date="2026-01-01")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        rec = SeqIO.read(io.StringIO(GenBankGenerator().generate(design)), "genbank")
    assert rec.name == "pET28a_GFP_const"
    assert len(rec) == len(seq)
    assert rec.annotations["topology"] == "circular"
