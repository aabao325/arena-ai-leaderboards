#!/usr/bin/env python3
"""
手动探测 arena.ai 新的三级分类结构
基于用户截图已知的信息：
- Image -> Image Edit Arena -> Single Image Edit / Multi Image Edit
- Multi Image Edit -> Overall / Product, Branding & Co... / 3D Imaging & Modeling / ...
"""
import sys
import json

sys.stdout.reconfigure(encoding='utf-8')

# 基于截图和已知信息，先手动构建结构框架
KNOWN_STRUCTURE = {
    "agent": {
        "subcategories": ["unknown1", "unknown2"]  # 需要探测
    },
    "chat": {
        "subcategories": ["text", "search", "vision", "document"]
    },
    "code": {
        "subcategories": ["unknown"]  # 需要探测
    },
    "image": {
        "subcategories": {
            "image-edit": {
                "url": "image/image-edit",
                "variants": ["single-image-edit", "multi-image-edit"],
                "multi-image-edit-subcats": [
                    "overall",
                    "product-branding-co",
                    "3d-imaging-modeling",
                    "cartoon-anime-fantasy",
                    "photorealistic-cinematography",
                    "art",
                    "portraits",
                    "text-rendering"
                ]
            },
            "text-to-image": {"url": "image/text-to-image"},
            "image-to-video": {"url": "image/image-to-video"}
        }
    },
    "video": {
        "subcategories": ["text-to-video", "video-edit"]  # 需要探测是否还有其他
    }
}

print("=== Arena.ai 新分类结构分析 ===\n")
print("1. 顶层分类（一级）:")
print("   Agent / Chat / Code / Image / Video")
print("\n2. Image 分类的详细结构（基于截图）:")
print("   Image/")
print("     ├─ Image Edit Arena/  # 新增容器分类")
print("     │   ├─ Single Image Edit")
print("     │   └─ Multi Image Edit/  # 又有三级子分类")
print("     │       ├─ Overall")
print("     │       ├─ Product, Branding & Co...")
print("     │       ├─ 3D Imaging & Modeling")
print("     │       ├─ Cartoon, Anime & Fantasy")
print("     │       ├─ Photorealistic & Cinematography")
print("     │       ├─ Art")
print("     │       ├─ Portraits")
print("     │       └─ Text Rendering")
print("     ├─ Text to Image")
print("     └─ Image to Video")
print("\n3. URL 模式推断:")
print("   一级/二级: https://arena.ai/leaderboard/{category}/{subcategory}")
print("   例如: https://arena.ai/leaderboard/image/image-edit")
print("\n4. 数据结构设计:")
print("   需要支持 3 级分类层级:")
print("   - category (Agent/Chat/Code/Image/Video)")
print("   - subcategory (image-edit, text-to-image, ...)")
print("   - variant (single-image-edit, multi-image-edit) [可选]")
print("   - subcat (overall, product-branding-co, ...) [可选，仅 multi-image-edit]")
print("\n5. UI 重构方向:")
print("   - 顶部：一级分类下拉（Agent/Chat/Code/Image/Video）")
print("   - 左侧：二级分类导航树（可折叠）")
print("   - 三级子分类：左侧缩进显示，点击切换表格内容")
print("   - 主区域：保持现有表格样式，但增加面包屑导航")

# 输出 JSON 供后续使用
with open('D:/aabao/Documents/GitHub/aabao-tools/_temp_arena/structure_draft.json', 'w', encoding='utf-8') as f:
    json.dump(KNOWN_STRUCTURE, f, ensure_ascii=False, indent=2)

print("\n✅ 结构草案已保存到 structure_draft.json")
