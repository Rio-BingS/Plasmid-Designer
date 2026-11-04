"""
输出生成模块

生成:
- GenBank 格式文件
- 引物订单表
- 设计报告
"""

import json
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass

from core.genbank_format import format_locus_line, render_origin_block


@dataclass
class PlasmidDesign:
    """质粒设计结果"""
    design_id: str
    design_name: str
    insert_sequence: str
    insert_name: str
    vector_id: str
    vector_name: str
    final_sequence: str  # 最终质粒序列
    primers: List[Dict]
    cloning_method: str
    design_date: str
    notes: str = ""
    
    @property
    def plasmid_length(self) -> int:
        return len(self.final_sequence)


class GenBankGenerator:
    """GenBank文件生成器"""
    
    def generate(
        self,
        design: PlasmidDesign,
        features: Optional[List[Dict]] = None,
        organism: str = "synthetic construct"
    ) -> str:
        """
        生成GenBank格式文件
        
        Args:
            design: 质粒设计
            features: 特征列表
            organism: 生物体名称
        
        Returns:
            GenBank格式字符串
        """
        if features is None:
            features = []
        
        lines = []
        
        # LOCUS
        locus_name = design.design_name[:16].replace(' ', '_')
        length = len(design.final_sequence)

        lines.append(format_locus_line(locus_name, length, topology="circular"))
        lines.append(f"DEFINITION  {design.design_name}")
        lines.append(f"ACCESSION   {design.design_id}")
        lines.append(f"VERSION     {design.design_id}.1")
        lines.append(f"KEYWORDS    .")
        lines.append(f"SOURCE      {organism}")
        lines.append(f"  ORGANISM  {organism}")
        lines.append(f"            Artificial sequence.")
        lines.append(f"FEATURES             Location/Qualifiers")
        lines.append(f"     source          1..{length}")
        lines.append(f'                     /organism="{organism}"')
        lines.append(f'                     /mol_type="other DNA"')
        
        # 添加特征
        for feature in features:
            feat_type = feature.get('type', 'misc_feature')
            start = feature.get('start', 1)
            end = feature.get('end', length)
            qualifier = feature.get('qualifier', '')
            
            if start > end:  # 环状序列，跨起点
                lines.append(f"     {feat_type:<16} join({end}..{length},1..{start})")
            else:
                lines.append(f"     {feat_type:<16} {start}..{end}")
            
            if qualifier:
                lines.append(f'                     /label="{qualifier}"')
        
        # 序列
        lines.append(f"ORIGIN")
        lines.extend(render_origin_block(design.final_sequence))

        lines.append(f"//")

        return '\n'.join(lines)


class PrimerOrderGenerator:
    """引物订单生成器"""
    
    def generate_excel(self, primers: List[Dict], output_path: str) -> str:
        """生成引物订单（TSV格式，可用Excel打开）"""
        lines = [
            "Name\tSequence\tLength\tTm\tGC%\tScale\tPurification\tNotes"
        ]
        
        for primer in primers:
            lines.append(
                f"{primer.get('name', '')}\t"
                f"{primer.get('sequence', '')}\t"
                f"{primer.get('length', len(primer.get('sequence', '')))}\t"
                f"{primer.get('tm', 'N/A')}\t"
                f"{primer.get('gc', 'N/A')}\t"
                f"{primer.get('scale', '25nmol')}\t"
                f"{primer.get('purification', 'STD')}\t"
                f"{primer.get('notes', '')}"
            )
        
        content = '\n'.join(lines)
        
        if output_path:
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(content)
        
        return content
    
    def generate_json(self, primers: List[Dict]) -> str:
        """生成JSON格式引物信息"""
        return json.dumps(primers, indent=2)


class DesignReportGenerator:
    """设计报告生成器"""
    
    def generate(
        self,
        design: PlasmidDesign,
        validation_result: Optional[Dict] = None,
        optimization_result: Optional[Dict] = None
    ) -> str:
        """生成设计报告（Markdown格式）"""
        lines = [
            f"# Plasmid Design Report",
            f"",
            f"## Design Information",
            f"",
            f"| Property | Value |",
            f"|----------|-------|",
            f"| Design ID | {design.design_id} |",
            f"| Design Name | {design.design_name} |",
            f"| Insert | {design.insert_name} |",
            f"| Vector | {design.vector_name} ({design.vector_id}) |",
            f"| Cloning Method | {design.cloning_method} |",
            f"| Final Length | {design.plasmid_length:,} bp |",
            f"| Design Date | {design.design_date} |",
            f"",
            f"## Sequence Information",
            f"",
            f"### Insert Sequence",
            f"```",
            f"{design.insert_sequence[:100]}{'...' if len(design.insert_sequence) > 100 else ''}",
            f"```",
            f"",
            f"- Length: {len(design.insert_sequence):,} bp",
            f"",
            f"## Primers",
            f"",
            f"| Name | Sequence | Length | Tm |",
            f"|------|----------|--------|-----|",
        ]
        
        for primer in design.primers:
            lines.append(
                f"| {primer.get('name', '')} | "
                f"`{primer.get('sequence', '')[:30]}{'...' if len(primer.get('sequence', '')) > 30 else ''}` | "
                f"{primer.get('length', '')} | "
                f"{primer.get('tm', '')} |"
            )
        
        if validation_result:
            lines.extend([
                f"",
                f"## Validation Results",
                f"",
            ])
            
            if validation_result.get('is_valid'):
                lines.append(f"✅ Sequence validation passed")
            else:
                lines.append(f"❌ Sequence validation failed")
            
            if validation_result.get('errors'):
                lines.append(f"")
                lines.append(f"### Errors")
                for err in validation_result['errors']:
                    lines.append(f"- {err}")
            
            if validation_result.get('warnings'):
                lines.append(f"")
                lines.append(f"### Warnings")
                for warn in validation_result['warnings']:
                    lines.append(f"- {warn}")
        
        if optimization_result:
            lines.extend([
                f"",
                f"## Optimization Results",
                f"",
                f"| Metric | Value |",
                f"|--------|-------|",
            ])
            
            for key, value in optimization_result.items():
                lines.append(f"| {key} | {value} |")
        
        lines.extend([
            f"",
            f"## Notes",
            f"",
            f"{design.notes if design.notes else 'No additional notes.'}",
            f"",
            f"---",
            f"*Report generated by Plasmid Designer*",
            f"*Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*",
        ])
        
        return '\n'.join(lines)


def create_default_features(
    insert_start: int,
    insert_end: int,
    insert_name: str,
    vector_features: List[Dict]
) -> List[Dict]:
    """创建默认特征注释"""
    features = []
    
    # 插入片段
    features.append({
        'type': 'CDS',
        'start': insert_start,
        'end': insert_end,
        'qualifier': insert_name
    })
    
    # 从载体继承的特征
    features.extend(vector_features)
    
    return features


def export_all(
    design: PlasmidDesign,
    output_dir: str,
    features: Optional[List[Dict]] = None
) -> Dict[str, str]:
    """
    导出所有格式
    
    Returns:
        {file_type: file_path}
    """
    import os
    os.makedirs(output_dir, exist_ok=True)
    
