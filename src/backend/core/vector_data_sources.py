"""
载体数据源配置和使用指南
"""

# ==================== 支持的数据源 ====================

SOURCES = {
    "addgene": {
        "name": "Addgene",
        "url": "https://www.addgene.org",
        "type": "API",
        "api_url": "https://api.addgene.org/v2",
        "requires_auth": True,
        "description": "全球最大的质粒共享库，超过 80,000 个载体",
        "free_tier": "公开数据可爬取，完整 API 需注册",
        "popular_ids": [
            "12259",  # pLenti-CMV-GFP-Puro
            "12252",  # pLenti-CMV-EGFP-FLAG-P2A-Puro
            "15860",  # pcDNA3.1(+)
            "6318",   # pET-28a(+)
            "39281",  # pGEX-6P-1
        ]
    },
    
    "snapgene": {
        "name": "SnapGene",
        "url": "https://www.snapgene.com",
        "type": "file_import",
        "formats": [".dna"],
        "description": "SnapGene 专有格式，包含完整图谱和注释",
        "free_tier": "需要 SnapGene 软件（商业软件）"
    },
    
    "genbank": {
        "name": "GenBank",
        "url": "https://www.ncbi.nlm.nih.gov/genbank/",
        "type": "file_import",
        "formats": [".gb", ".gbk"],
        "description": "NCBI 标准序列格式，广泛支持",
        "free_tier": "完全免费"
    },
    
    "benchling": {
        "name": "Benchling",
        "url": "https://benchling.com",
        "type": "file_export",
        "formats": [".gb", ".fasta", ".json"],
        "description": "云平台，可导出 GenBank 格式",
        "free_tier": "个人账户免费"
    },
    
    "igem": {
        "name": "iGEM Registry",
        "url": "http://parts.igem.org",
        "type": "api",
        "api_url": "http://parts.igem.org/cgi/xml/part.cgi",
        "description": "iGEM 标准生物元件库",
        "free_tier": "公开数据，可爬取"
    }
}


# ==================== 使用示例 ====================

"""
## 1. 从 Addgene 导入

需要先注册 Addgene 账号并申请 API key:

```python
from core.external_vector_importer import AddgeneClient, VectorLibraryManager

# 使用 API key
client = AddgeneClient(api_key="your-api-key")
vectors = client.search_vectors("pET")

# 或使用公开数据（简化版）
manager = VectorLibraryManager()
results = manager.search_online("GFP")
```

## 2. 导入 GenBank 文件

从 NCBI 下载 .gb 文件后:

```python
from core.external_vector_importer import import_genbank_file, batch_import_genbank

# 单个文件
vector = import_genbank_file("/path/to/plasmid.gb")

# 批量导入
vectors = batch_import_genbank("/path/to/genbank_files/")
```

## 3. 导入 SnapGene 文件

需要先在 SnapGene 中创建或下载 .dna 文件:

```python
from core.external_vector_importer import import_snapgene_file

vector = import_snapgene_file("/path/to/plasmid.dna")
```

## 4. 从 Benchling 导出

在 Benchling 中:
1. 打开质粒序列
2. 点击 "Export" → "GenBank"
3. 下载 .gb 文件
4. 使用 GenBank 导入器导入

## 5. 从 iGEM 获取

```python
# iGEM 数据通过网页爬取
# 常用元件:
# - BBa_J23100 (强启动子)
# - BBa_B0034 (RBS)
# - BBa_E0040 (GFP)
# - BBa_B0015 (终止子)

# 在浏览器访问:
# http://parts.igem.org/Part:BBa_E0040
# 下载 GenBank 格式后导入
"""


# ==================== 常用载体 ID 参考 ====================

POPULAR_VECTORS = {
    # E. coli 表达载体
    "expression_ecoli": [
        {"name": "pET-28a(+)", "addgene_id": "6318", "features": "N-His, T7"},
        {"name": "pET-21a(+)", "addgene_id": "69745", "features": "C-His, T7"},
        {"name": "pET-22b(+)", "addgene_id": "69746", "features": "pelB, C-His"},
        {"name": "pGEX-6P-1", "addgene_id": "39281", "features": "GST, PreScission"},
        {"name": "pMAL-c5X", "addgene_id": "82310", "features": "MBP"},
    ],
    
    # 哺乳动物表达载体
    "expression_mammalian": [
        {"name": "pcDNA3.1(+)", "addgene_id": "129796", "features": "CMV"},
        {"name": "pcDNA3.1/Hygro(+)", "addgene_id": "79664", "features": "CMV, HygR"},
        {"name": "pCMV-Tag2B", "addgene_id": "6310", "features": "CMV, FLAG"},
    ],
    
    # 慢病毒载体
    "lentiviral": [
        {"name": "pLenti-CMV-GFP-Puro", "addgene_id": "12259", "features": "CMV, GFP, Puro"},
        {"name": "pLenti-CMV-EGFP-FLAG-P2A-Puro", "addgene_id": "12252", "features": "CMV, P2A"},
    ],
    
    # CRISPR 载体
    "crispr": [
        {"name": "pSpCas9(BB)-2A-GFP", "addgene_id": "48138", "features": "Cas9, GFP"},
        {"name": "pSpCas9(BB)-2A-Puro", "addgene_id": "62988", "features": "Cas9, Puro"},
        {"name": "pX458", "addgene_id": "48138", "features": "Cas9, GFP"},
    ],
    
    # 昆虫细胞表达
    "insect": [
        {"name": "pFastBac1", "addgene_id": "71279", "features": "Baculovirus"},
    ],
}


# ==================== 数据导入最佳实践 ====================

BEST_PRACTICES = """
## 载体数据导入最佳实践

### 1. 数据来源选择

| 来源 | 适用场景 | 优点 | 缺点 |
|------|----------|------|------|
| Addgene | 研究级载体 | 序列准确、注释完整 | 需要注册 |
| GenBank | 标准格式导入 | 免费、通用 | 需手动下载 |
| SnapGene | 图谱编辑 | 可视化完整 | 商业软件 |
| iGEM | 标准元件 | 开源、标准化 | 元件碎片化 |

### 2. 导入流程

1. **验证来源**
   - 确认载体序列来源可信
   - 检查是否有发表文献支持

2. **检查序列**
   - 确认完整环状序列
   - 检查注释是否准确
   - 验证关键元件位置

3. **统一格式**
   - 导入后转为 YAML 统一存储
   - 保留原始格式作为备份
   - 记录导入来源和时间

### 3. 质量控制

- [ ] 序列长度匹配预期
- [ ] 关键元件位置正确
- [ ] 无内部冲突位点
- [ ] 抗性基因完整
- [ ] 复制起点正确

### 4. 定期更新

- 定期同步 Addgene 更新
- 检查载体勘误信息
- 维护本地数据库版本记录
"""

# ==================== 快速导入脚本 ====================

QUICK_IMPORT_SCRIPT = """
#!/usr/bin/env python3
'''
快速导入脚本 - 从各种来源导入载体
'''

import sys
from core.paths import BACKEND_DIR
sys.path.insert(0, str(BACKEND_DIR))

from core.external_vector_importer import (
    VectorLibraryManager,
    import_genbank_file,
    import_snapgene_file
)

def main():
    manager = VectorLibraryManager()
    
    print("载体导入工具")
    print("1. 导入 GenBank 文件")
    print("2. 导入 SnapGene 文件")
    print("3. 批量导入目录")
    print("4. 搜索 Addgene")
    
    choice = input("选择操作: ")
    
    if choice == "1":
        filepath = input("GenBank 文件路径: ")
        vector = import_genbank_file(filepath)
        print(f"已导入: {vector.name}")
        
    elif choice == "2":
        filepath = input("SnapGene 文件路径: ")
        vector = import_snapgene_file(filepath)
        print(f"已导入: {vector.name}")
        
    elif choice == "3":
        directory = input("目录路径: ")
        count = manager.import_from_genbank(directory)
        print(f"已导入 {count} 个载体")
        
    elif choice == "4":
        query = input("搜索关键词: ")
        results = manager.search_online(query)
        for v in results[:10]:
            print(f"- {v.name} ({v.url})")

if __name__ == "__main__":
    main()
"""
