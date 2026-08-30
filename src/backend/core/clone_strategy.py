"""
克隆策略模块

支持:
- Gibson Assembly 克隆策略
- Golden Gate 克隆策略
- 限制性酶切克隆策略
- 全基因合成策略
"""

import math
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum


class CloningMethod(Enum):
    GIBSON = "gibson_assembly"
    GOLDEN_GATE = "golden_gate"
    RESTRICTION = "restriction_cloning"
    IN_FUSION = "in_fusion"
    GENE_SYNTHESIS = "gene_synthesis"


@dataclass
class CloningStep:
    """克隆步骤"""
    step_number: int
    action: str
    description: str
    description_zh: str = ""
    reagents: List[str] = field(default_factory=list)
    reagents_zh: List[str] = field(default_factory=list)
    conditions: Dict[str, str] = field(default_factory=dict)
    duration: str = ""
    duration_zh: str = ""
    notes: str = ""
    notes_zh: str = ""


# 试剂英文 -> 中文映射
_REAGENT_ZH_MAP = {
    "PCR Master Mix": "PCR预混液",
    "Template DNA": "模板DNA",
    "Forward primer": "正向引物",
    "Reverse primer": "反向引物",
    "Vector plasmid": "载体质粒",
    "PCR reagents or restriction enzymes": "PCR试剂或限制性内切酶",
    "Agarose gel": "琼脂糖凝胶",
    "Gel extraction kit": "胶回收试剂盒",
    "Gibson Assembly Master Mix": "Gibson Assembly预混液",
    "Insert DNA": "插入片段DNA",
    "Linearized vector": "线性化载体",
    "Competent E. coli": "大肠杆菌感受态细胞",
    "LB agar plates with antibiotic": "含抗生素LB琼脂平板",
    "Screening primers or restriction enzymes": "筛选引物或限制性内切酶",
    "Screening primers": "筛选引物",
    "T4 DNA Ligase": "T4 DNA连接酶",
    "ATP": "ATP",
    "Insert PCR product": "插入片段PCR产物",
    "Destination vector": "目标载体",
    "Insert": "插入片段",
    "Vector": "载体",
    "Ligation buffer": "连接缓冲液",
    "CutSmart buffer": "CutSmart缓冲液",
    "Oligo design software": "寡核苷酸设计软件",
    "Target sequence": "目标序列",
    "Oligo pool or individual oligos": "寡核苷酸池或单条寡核苷酸",
    "Oligo pool": "寡核苷酸池",
    "Assembly buffer": "组装缓冲液",
    "Gibson Assembly Master Mix or restriction enzymes and ligase": "Gibson Assembly预混液或限制性内切酶和连接酶",
    "Sequencing service": "测序服务",
}

# 时长英文 -> 中文映射
_DURATION_ZH_MAP = {
    "~1.5 hours": "约1.5小时",
    "1-2 hours": "1-2小时",
    "30-45 min": "30-45分钟",
    "15-60 min": "15-60分钟",
    "~2 hours": "约2小时",
    "1 day": "1天",
    "~30 min": "约30分钟",
    "2-5 business days": "2-5个工作日",
    "~3 hours": "约3小时",
    "1-16 hours": "1-16小时",
    "1-3 days": "1-3天",
}

# 警告英文 -> 中文映射
_WARNING_ZH_MAP = {
    "Ensure overhang sequences are unique and don't self-anneal": "确保overhang序列唯一且不会自退火",
    "Check for internal BsaI/BsmBI sites in insert sequence": "检查插入片段内部是否存在BsaI/BsmBI位点",
    "Check reading frame after insertion": "检查插入后的阅读框",
    "Dephosphorylate vector to reduce background": "载体去磷酸化以降低背景",
    "Ensure all oligos have correct overlap sequences": "确保所有寡核苷酸重叠序列正确",
    "Check for secondary structures in oligo design": "检查寡核苷酸设计中的二级结构",
    "Verify assembled sequence by full-length Sanger sequencing": "通过全长Sanger测序验证组装序列",
    "Consider codon optimization for expression host": "考虑对表达宿主进行密码子优化",
}

# 条件值英文 -> 中文映射
_CONDITION_VAL_ZH_MAP = {
    "PCR amplification or restriction digest": "PCR扩增或限制性酶切",
    "Follow enzyme manufacturer protocol": "遵循酶制造商操作说明",
    "Room temperature or 16°C": "室温或16°C",
    "1 hour to overnight": "1小时至过夜",
    "Add CIP/SAP if needed": "如需要可添加CIP/SAP",
    "Standard desalting or PAGE": "标准脱盐或PAGE纯化",
    "Gibson Assembly recommended for seamless cloning": "推荐Gibson Assembly实现无缝克隆",
    "Add outer primers for amplification": "添加外侧引物进行扩增",
    "20 cycles with outer primers": "使用外侧引物循环20次",
}


def _reagent_zh(reagent: str) -> str:
    return _REAGENT_ZH_MAP.get(reagent, reagent)


def _duration_zh(duration: str) -> str:
    return _DURATION_ZH_MAP.get(duration, duration)


def _warning_zh(warning: str) -> str:
    return _WARNING_ZH_MAP.get(warning, warning)


def _condition_val_zh(val: str) -> str:
    return _CONDITION_VAL_ZH_MAP.get(val, val)


# 步骤 action 英文 -> 中文映射
_ACTION_ZH_MAP = {
    "PCR amplify insert": "PCR扩增插入片段",
    "Linearize vector": "线性化载体",
    "Gel purification": "胶回收纯化",
    "Gibson Assembly": "Gibson Assembly组装",
    "Transformation": "转化",
    "Colony screening": "菌落筛选",
    "Golden Gate reaction": "Golden Gate反应",
    "Digest insert": "酶切插入片段",
    "Digest vector": "酶切载体",
    "Ligation": "连接",
    "Design oligos": "设计寡核苷酸",
    "Order oligos": "订购寡核苷酸",
    "Assembly PCR": "组装PCR",
    "Clone into vector": "克隆到载体",
}


def _action_zh(action: str) -> str:
    return _ACTION_ZH_MAP.get(action, action)


# 条件 key 英文 -> 中文映射
_CONDITION_ZH_MAP = {
    "Initial denaturation": "初始变性",
    "Cycling (30 cycles)": "循环 (30个循环)",
    "Final extension": "最终延伸",
    "Method": "方法",
    "If digest": "如果酶切",
    "Insert:Vector ratio": "插入:载体比例",
    "Incubation": "孵育",
    "Heat shock": "热激",
    "Recovery": "复苏",
    "Cycling (25-50 cycles)": "循环 (25-50个循环)",
    "Final digestion": "最终酶切",
    "Heat inactivation": "热失活",
    "Temperature": "温度",
    "Time": "时间",
    "Dephosphorylation": "去磷酸化",
    "Oligo length": "寡核苷酸长度",
    "Overlap length": "重叠长度",
    "Purification": "纯化方式",
    "Scale": "合成规模",
    "Extension": "延伸",
    "Additional cycles": "额外循环",
    "Cycling (15 cycles)": "循环 (15个循环)",
}


def _condition_key_zh(key: str) -> str:
    return _CONDITION_ZH_MAP.get(key, key)


@dataclass
class CloningStrategy:
    """克隆策略"""
    method: CloningMethod
    insert_name: str
    vector_name: str
    steps: List[CloningStep] = field(default_factory=list)
    primers: List[dict] = field(default_factory=list)
    enzymes: List[str] = field(default_factory=list)
    expected_product_size: int = 0
    warnings: List[str] = field(default_factory=list)
    warnings_zh: List[str] = field(default_factory=list)

    def to_protocol(self, language: str = "zh") -> str:
        """生成实验方案文本

        Args:
            language: "zh" 中文 (默认), "en" 英文
        """
        if language == "zh":
            return self._to_protocol_zh()
        return self._to_protocol_en()

    def _to_protocol_zh(self) -> str:
        """生成中文实验方案"""
        method_names = {
            "gibson_assembly": "Gibson Assembly 克隆",
            "golden_gate": "Golden Gate 克隆",
            "restriction_cloning": "限制性酶切克隆",
            "in_fusion": "In-Fusion 克隆",
            "gene_synthesis": "全基因合成",
        }
        method_name = method_names.get(self.method.value, self.method.value)

        lines = [
            f"# {method_name}实验方案",
            f"",
            f"## 概述",
            f"- 插入片段: {self.insert_name}",
            f"- 载体: {self.vector_name}",
            f"- 预期产物大小: {self.expected_product_size} bp",
            f"",
            f"## 材料",
        ]

        if self.enzymes:
            lines.append(f"酶/试剂: {', '.join(self.enzymes)}")

        if self.primers:
            lines.append("引物:")
            for p in self.primers:
                lines.append(f" - {p['name']}: {p['sequence']}")

        lines.append(f"")
        lines.append(f"## 操作步骤")

        for step in self.steps:
            action_zh = _action_zh(step.action)
            lines.append(f"")
            lines.append(f"### 步骤 {step.step_number}: {action_zh}")

            # 描述：优先使用 description_zh，否则翻译 description
            if step.description_zh:
                lines.append(step.description_zh)
            else:
                lines.append(step.description)

            # 试剂：优先使用 reagents_zh，否则逐个翻译
            if step.reagents:
                if step.reagents_zh:
                    reagents_display = step.reagents_zh
                else:
                    reagents_display = [_reagent_zh(r) for r in step.reagents]
                lines.append(f"试剂: {', '.join(reagents_display)}")

            # 条件：翻译 key 和 value
            if step.conditions:
                for k, v in step.conditions.items():
                    v_zh = _condition_val_zh(v)
                    lines.append(f"- {_condition_key_zh(k)}: {v_zh}")

            # 时长
            if step.duration:
                dur_zh = step.duration_zh if step.duration_zh else _duration_zh(step.duration)
                lines.append(f"时长: {dur_zh}")

            # 备注
            if step.notes:
                notes_zh = step.notes_zh if step.notes_zh else step.notes
                lines.append(f"备注: {notes_zh}")

        # 警告
        if self.warnings:
            lines.append(f"")
            lines.append(f"## 注意事项")
            for i, w in enumerate(self.warnings):
                if self.warnings_zh and i < len(self.warnings_zh):
                    lines.append(f"- {self.warnings_zh[i]}")
                else:
                    lines.append(f"- {_warning_zh(w)}")

        return '\n'.join(lines)

    def _to_protocol_en(self) -> str:
        """生成英文实验方案"""
        lines = [
            f"# {self.method.value.replace('_', ' ').title()} Protocol",
            f"",
            f"## Overview",
            f"- Insert: {self.insert_name}",
            f"- Vector: {self.vector_name}",
            f"- Expected product size: {self.expected_product_size} bp",
            f"",
            f"## Materials",
        ]

        if self.enzymes:
            lines.append(f"Enzymes: {', '.join(self.enzymes)}")

        if self.primers:
            lines.append("Primers:")
            for p in self.primers:
                lines.append(f" - {p['name']}: {p['sequence']}")

        lines.append(f"")
        lines.append(f"## Procedure")

        for step in self.steps:
            lines.append(f"")
            lines.append(f"### Step {step.step_number}: {step.action}")
            lines.append(f"{step.description}")
            if step.reagents:
                lines.append(f"Reagents: {', '.join(step.reagents)}")
            if step.conditions:
                for k, v in step.conditions.items():
                    lines.append(f"- {k}: {v}")
            if step.duration:
                lines.append(f"Duration: {step.duration}")
            if step.notes:
                lines.append(f"Note: {step.notes}")

        if self.warnings:
            lines.append(f"")
            lines.append(f"## Warnings")
            for w in self.warnings:
                lines.append(f"- {w}")

        return '\n'.join(lines)


class GibsonAssemblyStrategy:
    """Gibson Assembly 策略生成器"""

    def generate(
        self,
        insert_seq: str,
        insert_name: str,
        vector_seq: str,
        vector_name: str,
        insert_position: int,
        homology_arm: int = 20
    ) -> CloningStrategy:
        """生成Gibson Assembly策略"""
        insert_len = len(insert_seq)
        vector_len = len(vector_seq)
        product_size = vector_len + insert_len

        steps = [
            CloningStep(
                step_number=1,
                action="PCR amplify insert",
                description=f"Amplify {insert_name} with Gibson primers",
                description_zh=f"使用Gibson引物扩增{insert_name}",
                reagents=["PCR Master Mix", "Template DNA", "Forward primer", "Reverse primer"],
                reagents_zh=["PCR预混液", "模板DNA", "正向引物", "反向引物"],
                conditions={
                    "Initial denaturation": "98°C, 30s",
                    "Cycling (30 cycles)": "98°C 10s, 60°C 15s, 72°C 30s/kb",
                    "Final extension": "72°C, 5min"
                },
                duration="~1.5 hours",
                duration_zh="约1.5小时",
                notes=f"Primers contain {homology_arm}bp homology arms",
                notes_zh=f"引物包含{homology_arm}bp同源臂"
            ),
            CloningStep(
                step_number=2,
                action="Linearize vector",
                description=f"Linearize {vector_name} by PCR or restriction digest",
                description_zh=f"通过PCR或限制性酶切线性化{vector_name}",
                reagents=["Vector plasmid", "PCR reagents or restriction enzymes"],
                reagents_zh=["载体质粒", "PCR试剂或限制性内切酶"],
                conditions={
                    "Method": "PCR amplification or restriction digest",
                    "If digest": "Follow enzyme manufacturer protocol"
                },
                duration="1-2 hours",
                duration_zh="1-2小时"
            ),
            CloningStep(
                step_number=3,
                action="Gel purification",
                description="Purify PCR products by gel extraction",
                description_zh="通过胶回收纯化PCR产物",
                reagents=["Agarose gel", "Gel extraction kit"],
                reagents_zh=["琼脂糖凝胶", "胶回收试剂盒"],
                duration="30-45 min",
                duration_zh="30-45分钟"
            ),
            CloningStep(
                step_number=4,
                action="Gibson Assembly",
                description="Assemble insert and vector using Gibson Assembly Master Mix",
                description_zh="使用Gibson Assembly预混液组装插入片段和载体",
                reagents=["Gibson Assembly Master Mix", "Insert DNA", "Linearized vector"],
                reagents_zh=["Gibson Assembly预混液", "插入片段DNA", "线性化载体"],
                conditions={
                    "Insert:Vector ratio": "2:1 to 3:1",
                    "Incubation": "50°C, 15-60 min"
                },
                duration="15-60 min",
                duration_zh="15-60分钟"
            ),
            CloningStep(
                step_number=5,
                action="Transformation",
                description="Transform assembly into competent E. coli",
                description_zh="将组装产物转化至大肠杆菌感受态细胞",
                reagents=["Competent E. coli", "LB agar plates with antibiotic"],
                reagents_zh=["大肠杆菌感受态细胞", "含抗生素LB琼脂平板"],
                conditions={
                    "Heat shock": "42°C, 45s",
                    "Recovery": "SOC medium, 37°C, 1 hour"
                },
                duration="~2 hours",
                duration_zh="约2小时"
            ),
            CloningStep(
                step_number=6,
                action="Colony screening",
                description="Screen colonies by colony PCR or restriction digest",
                description_zh="通过菌落PCR或限制性酶切筛选菌落",
                reagents=["Screening primers or restriction enzymes"],
                reagents_zh=["筛选引物或限制性内切酶"],
                duration="1 day",
                duration_zh="1天"
            )
        ]

        return CloningStrategy(
            method=CloningMethod.GIBSON,
            insert_name=insert_name,
            vector_name=vector_name,
            steps=steps,
            enzymes=["Gibson Assembly Master Mix"],
            expected_product_size=product_size,
            warnings=[],
            warnings_zh=[]
        )


class GoldenGateStrategy:
    """Golden Gate 策略生成器"""

    def generate(
        self,
        insert_seq: str,
        insert_name: str,
        vector_seq: str,
        vector_name: str,
        enzyme: str = "BsaI",
        overhang_5: str = "AATG",
        overhang_3: str = "GCTT"
    ) -> CloningStrategy:
        """生成Golden Gate策略"""
        insert_len = len(insert_seq)
        vector_len = len(vector_seq)
        product_size = vector_len + insert_len

        steps = [
            CloningStep(
                step_number=1,
                action="PCR amplify insert",
                description=f"Amplify {insert_name} with Golden Gate primers",
                description_zh=f"使用Golden Gate引物扩增{insert_name}",
                reagents=["PCR Master Mix", "Template DNA", "Forward primer", "Reverse primer"],
                reagents_zh=["PCR预混液", "模板DNA", "正向引物", "反向引物"],
                conditions={
                    "Initial denaturation": "98°C, 30s",
                    "Cycling (30 cycles)": "98°C 10s, 60°C 15s, 72°C 30s/kb",
                    "Final extension": "72°C, 5min"
                },
                duration="~1.5 hours",
                duration_zh="约1.5小时",
                notes=f"Primers contain {enzyme} site and custom overhangs",
                notes_zh=f"引物包含{enzyme}位点和自定义overhang"
            ),
            CloningStep(
                step_number=2,
                action="Golden Gate reaction",
                description="One-pot digestion and ligation",
                description_zh="一管法酶切和连接反应",
                reagents=[
                    f"{enzyme} (Type IIS restriction enzyme)",
                    "T4 DNA Ligase",
                    "ATP",
                    "Insert PCR product",
                    "Destination vector"
                ],
                reagents_zh=[
                    f"{enzyme} (IIS型限制性内切酶)",
                    "T4 DNA连接酶",
                    "ATP",
                    "插入片段PCR产物",
                    "目标载体"
                ],
                conditions={
                    "Cycling (25-50 cycles)": "37°C 2min, 16°C 5min",
                    "Final digestion": "50°C, 10min",
                    "Heat inactivation": "80°C, 10min"
                },
                duration="~2 hours",
                duration_zh="约2小时",
                notes="No gel purification required if using clean PCR product",
                notes_zh="如果PCR产物纯度良好，无需胶回收"
            ),
            CloningStep(
                step_number=3,
                action="Transformation",
                description="Transform reaction into competent E. coli",
                description_zh="将反应产物转化至大肠杆菌感受态细胞",
                reagents=["Competent E. coli", "LB agar plates with antibiotic"],
                reagents_zh=["大肠杆菌感受态细胞", "含抗生素LB琼脂平板"],
                conditions={
                    "Heat shock": "42°C, 45s",
                    "Recovery": "SOC medium, 37°C, 1 hour"
                },
                duration="~2 hours",
                duration_zh="约2小时"
            ),
            CloningStep(
                step_number=4,
                action="Colony screening",
                description="Screen colonies by colony PCR",
                description_zh="通过菌落PCR筛选菌落",
                reagents=["Screening primers"],
                reagents_zh=["筛选引物"],
                duration="1 day",
                duration_zh="1天"
            )
        ]

        warnings_en = [
            "Ensure overhang sequences are unique and don't self-anneal",
            "Check for internal BsaI/BsmBI sites in insert sequence"
        ]
        warnings_zh = [
            "确保overhang序列唯一且不会自退火",
            "检查插入片段内部是否存在BsaI/BsmBI位点"
        ]

        return CloningStrategy(
            method=CloningMethod.GOLDEN_GATE,
            insert_name=insert_name,
            vector_name=vector_name,
            steps=steps,
            enzymes=[enzyme, "T4 DNA Ligase"],
            expected_product_size=product_size,
            warnings=warnings_en,
            warnings_zh=warnings_zh
        )


class RestrictionCloningStrategy:
    """限制性酶切克隆策略生成器"""

    def generate(
        self,
        insert_seq: str,
        insert_name: str,
        vector_seq: str,
        vector_name: str,
        enzyme_5: str,
        enzyme_3: str,
        dephosphorylate: bool = True
    ) -> CloningStrategy:
        """生成限制性酶切克隆策略"""
        insert_len = len(insert_seq)
        vector_len = len(vector_seq)
        product_size = vector_len + insert_len

        steps = [
            CloningStep(
                step_number=1,
                action="PCR amplify insert",
                description=f"Amplify {insert_name} with restriction site primers",
                description_zh=f"使用含酶切位点的引物扩增{insert_name}",
                reagents=["PCR Master Mix", "Template DNA", "Forward primer", "Reverse primer"],
                reagents_zh=["PCR预混液", "模板DNA", "正向引物", "反向引物"],
                conditions={
                    "Initial denaturation": "98°C, 30s",
                    "Cycling (30 cycles)": "98°C 10s, {Tm-5}°C 15s, 72°C 30s/kb",
                    "Final extension": "72°C, 5min"
                },
                duration="~1.5 hours",
                duration_zh="约1.5小时",
                notes=f"Primers contain {enzyme_5} and {enzyme_3} sites",
                notes_zh=f"引物包含{enzyme_5}和{enzyme_3}酶切位点"
            ),
            CloningStep(
                step_number=2,
                action="Digest insert",
                description=f"Digest PCR product with {enzyme_5} and {enzyme_3}",
                description_zh=f"使用{enzyme_5}和{enzyme_3}酶切PCR产物",
                reagents=[enzyme_5, enzyme_3, "Insert PCR product", "CutSmart buffer"],
                reagents_zh=[enzyme_5, enzyme_3, "插入片段PCR产物", "CutSmart缓冲液"],
                conditions={
                    "Temperature": "37°C",
                    "Time": "1-2 hours"
                },
                duration="1-2 hours",
                duration_zh="1-2小时"
            ),
            CloningStep(
                step_number=3,
                action="Digest vector",
                description=f"Digest {vector_name} with {enzyme_5} and {enzyme_3}",
                description_zh=f"使用{enzyme_5}和{enzyme_3}酶切{vector_name}",
                reagents=[enzyme_5, enzyme_3, "Vector plasmid", "CutSmart buffer"],
                reagents_zh=[enzyme_5, enzyme_3, "载体质粒", "CutSmart缓冲液"],
                conditions={
                    "Temperature": "37°C",
                    "Time": "1-2 hours",
                    "Dephosphorylation": "Add CIP/SAP if needed"
                },
                duration="1-2 hours",
                duration_zh="1-2小时"
            ),
            CloningStep(
                step_number=4,
                action="Gel purification",
                description="Purify digested insert and vector by gel extraction",
                description_zh="通过胶回收纯化酶切后的插入片段和载体",
                reagents=["Agarose gel", "Gel extraction kit"],
                reagents_zh=["琼脂糖凝胶", "胶回收试剂盒"],
                duration="30-45 min",
                duration_zh="30-45分钟"
            ),
            CloningStep(
                step_number=5,
                action="Ligation",
                description="Ligate insert into vector",
                description_zh="将插入片段连接到载体中",
                reagents=["T4 DNA Ligase", "Insert", "Vector", "Ligation buffer"],
                reagents_zh=["T4 DNA连接酶", "插入片段", "载体", "连接缓冲液"],
                conditions={
                    "Insert:Vector ratio": "3:1",
                    "Temperature": "Room temperature or 16°C",
                    "Time": "1 hour to overnight"
                },
                duration="1-16 hours",
                duration_zh="1-16小时"
            ),
            CloningStep(
                step_number=6,
                action="Transformation",
                description="Transform ligation into competent E. coli",
                description_zh="将连接产物转化至大肠杆菌感受态细胞",
                reagents=["Competent E. coli", "LB agar plates with antibiotic"],
                reagents_zh=["大肠杆菌感受态细胞", "含抗生素LB琼脂平板"],
                conditions={
                    "Heat shock": "42°C, 45s",
                    "Recovery": "SOC medium, 37°C, 1 hour"
                },
                duration="~2 hours",
                duration_zh="约2小时"
            ),
            CloningStep(
                step_number=7,
                action="Colony screening",
                description="Screen colonies by colony PCR or restriction digest",
                description_zh="通过菌落PCR或限制性酶切筛选菌落",
                reagents=["Screening primers or restriction enzymes"],
                reagents_zh=["筛选引物或限制性内切酶"],
                duration="1 day",
                duration_zh="1天"
            )
        ]

        warnings_en = [
            f"Verify {enzyme_5} and {enzyme_3} sites are not present within insert sequence",
            "Check reading frame after insertion",
            "Dephosphorylate vector to reduce background"
        ]
        warnings_zh = [
            f"确认插入片段内部不存在{enzyme_5}和{enzyme_3}位点",
            "检查插入后的阅读框",
            "载体去磷酸化以降低背景"
        ]

        return CloningStrategy(
            method=CloningMethod.RESTRICTION,
            insert_name=insert_name,
            vector_name=vector_name,
            steps=steps,
            enzymes=[enzyme_5, enzyme_3, "T4 DNA Ligase"],
            expected_product_size=product_size,
            warnings=warnings_en,
            warnings_zh=warnings_zh
        )


class GeneSynthesisStrategy:
    """全基因合成策略生成器"""

    def generate(
        self,
        insert_seq: str,
        insert_name: str,
        vector_seq: str,
        vector_name: str,
        oligo_length: int = 60,
        overlap_length: int = 20
    ) -> CloningStrategy:
        """生成全基因合成策略"""
        insert_len = len(insert_seq)
        vector_len = len(vector_seq)
        product_size = vector_len + insert_len
        num_oligos = math.ceil(insert_len / (oligo_length - overlap_length))

        steps = [
            CloningStep(
                step_number=1,
                action="Design oligos",
                description=f"Design {num_oligos} overlapping oligos for {insert_name} synthesis",
                description_zh=f"为{insert_name}合成设计{num_oligos}条重叠寡核苷酸",
                reagents=["Oligo design software", "Target sequence"],
                reagents_zh=["寡核苷酸设计软件", "目标序列"],
                conditions={
                    "Oligo length": f"{oligo_length} bp",
                    "Overlap length": f"{overlap_length} bp"
                },
                duration="~30 min",
                duration_zh="约30分钟",
                notes=f"Total {num_oligos} oligos needed for {insert_len}bp gene",
                notes_zh=f"合成{insert_len}bp基因共需{num_oligos}条寡核苷酸"
            ),
            CloningStep(
                step_number=2,
                action="Order oligos",
                description="Order designed oligos from synthesis provider",
                description_zh="从合成服务商订购设计的寡核苷酸",
                reagents=["Oligo pool or individual oligos"],
                reagents_zh=["寡核苷酸池或单条寡核苷酸"],
                conditions={
                    "Purification": "Standard desalting or PAGE",
                    "Scale": "10-25 nmol"
                },
                duration="2-5 business days",
                duration_zh="2-5个工作日"
            ),
            CloningStep(
                step_number=3,
                action="Assembly PCR",
                description="Assemble oligos into full-length gene by overlap extension PCR",
                description_zh="通过重叠延伸PCR将寡核苷酸组装为全长基因",
                reagents=["Oligo pool", "PCR Master Mix", "Assembly buffer"],
                reagents_zh=["寡核苷酸池", "PCR预混液", "组装缓冲液"],
                conditions={
                    "Initial denaturation": "98°C, 30s",
                    "Cycling (15 cycles)": "98°C 10s, 55°C 15s, 72°C 30s/kb",
                    "Extension": "Add outer primers for amplification",
                    "Additional cycles": "20 cycles with outer primers"
                },
                duration="~3 hours",
                duration_zh="约3小时",
                notes="Use outer primers to amplify the assembled product",
                notes_zh="使用外侧引物扩增组装产物"
            ),
            CloningStep(
                step_number=4,
                action="Gel purification",
                description="Purify the assembled PCR product by gel extraction",
                description_zh="通过胶回收纯化组装后的PCR产物",
                reagents=["Agarose gel", "Gel extraction kit"],
                reagents_zh=["琼脂糖凝胶", "胶回收试剂盒"],
                duration="30-45 min",
                duration_zh="30-45分钟"
            ),
            CloningStep(
                step_number=5,
                action="Clone into vector",
                description=f"Clone assembled gene into {vector_name} using Gibson Assembly or restriction cloning",
                description_zh=f"使用Gibson Assembly或限制性酶切将组装基因克隆到{vector_name}",
                reagents=["Gibson Assembly Master Mix or restriction enzymes and ligase", "Vector", "Insert"],
                reagents_zh=["Gibson Assembly预混液或限制性内切酶和连接酶", "载体", "插入片段"],
                conditions={
                    "Method": "Gibson Assembly recommended for seamless cloning",
                    "Insert:Vector ratio": "2:1 to 3:1"
                },
                duration="1-2 hours",
                duration_zh="1-2小时"
            ),
            CloningStep(
                step_number=6,
                action="Transformation",
                description="Transform into competent E. coli",
                description_zh="转化至大肠杆菌感受态细胞",
                reagents=["Competent E. coli", "LB agar plates with antibiotic"],
                reagents_zh=["大肠杆菌感受态细胞", "含抗生素LB琼脂平板"],
                conditions={
                    "Heat shock": "42°C, 45s",
                    "Recovery": "SOC medium, 37°C, 1 hour"
                },
                duration="~2 hours",
                duration_zh="约2小时"
            ),
            CloningStep(
                step_number=7,
                action="Colony screening",
                description="Screen colonies by colony PCR and confirm by sequencing",
                description_zh="通过菌落PCR筛选菌落并测序确认",
                reagents=["Screening primers", "Sequencing service"],
                reagents_zh=["筛选引物", "测序服务"],
                duration="1-3 days",
                duration_zh="1-3天",
                notes="Full-length sequencing is essential to verify synthesis accuracy",
                notes_zh="全长测序对于验证合成准确性至关重要"
            )
        ]

        warnings_en = [
            "Ensure all oligos have correct overlap sequences",
            "Check for secondary structures in oligo design",
            "Verify assembled sequence by full-length Sanger sequencing",
            "Consider codon optimization for expression host"
        ]
        warnings_zh = [
            "确保所有寡核苷酸重叠序列正确",
            "检查寡核苷酸设计中的二级结构",
            "通过全长Sanger测序验证组装序列",
            "考虑对表达宿主进行密码子优化"
        ]

        return CloningStrategy(
            method=CloningMethod.GENE_SYNTHESIS,
            insert_name=insert_name,
            vector_name=vector_name,
            steps=steps,
            enzymes=["Overlapping oligo pool", "High-fidelity DNA polymerase"],
            expected_product_size=product_size,
            warnings=warnings_en,
            warnings_zh=warnings_zh
        )


def generate_cloning_strategy(
    method: CloningMethod,
    insert_seq: str,
    insert_name: str,
    vector_seq: str,
    vector_name: str,
    **kwargs
) -> CloningStrategy:
    """
    克隆策略生成入口

    Args:
        method: 克隆方法
        insert_seq: 插入序列
        insert_name: 插入片段名称
        vector_seq: 载体序列
        vector_name: 载体名称
        **kwargs: 方法特定参数

    Returns:
        CloningStrategy 克隆策略
    """
    if method == CloningMethod.GIBSON:
        generator = GibsonAssemblyStrategy()
        return generator.generate(
            insert_seq, insert_name, vector_seq, vector_name,
            insert_position=kwargs.get('insert_position', 0),
            homology_arm=kwargs.get('homology_arm', 20)
        )

    elif method == CloningMethod.GOLDEN_GATE:
        generator = GoldenGateStrategy()
        return generator.generate(
            insert_seq, insert_name, vector_seq, vector_name,
            enzyme=kwargs.get('enzyme', 'BsaI'),
            overhang_5=kwargs.get('overhang_5', 'AATG'),
            overhang_3=kwargs.get('overhang_3', 'GCTT')
        )

    elif method == CloningMethod.RESTRICTION:
        generator = RestrictionCloningStrategy()
        return generator.generate(
            insert_seq, insert_name, vector_seq, vector_name,
            enzyme_5=kwargs.get('enzyme_5', 'EcoRI'),
            enzyme_3=kwargs.get('enzyme_3', 'XhoI'),
            dephosphorylate=kwargs.get('dephosphorylate', True)
        )

    elif method == CloningMethod.GENE_SYNTHESIS:
        generator = GeneSynthesisStrategy()
        return generator.generate(
            insert_seq, insert_name, vector_seq, vector_name,
            oligo_length=kwargs.get('oligo_length', 60),
            overlap_length=kwargs.get('overlap_length', 20)
        )

    else:
        raise ValueError(f"Unsupported cloning method: {method}")
