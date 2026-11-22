# SPDX-License-Identifier: GPL-3.0-or-later
"""Bilingual (EN / ZH) strings for the GOO port UI, menus and tools.

Every module in this package keeps its *own* string table close to the code.
This module is the shared table for the UI layer owned by the tools agent:
the node *Add* menu, the node library / tools panels, the Grease Pencil
helpers, the F-Curve isolate tool and the viewport line width overlay.

Keys are prefixed with ``goo.``.  Strings are registered at import time and
again from :func:`register` so a plain ``import goo_npr.i18n_strings`` is
enough for panel/menu code that is imported afterwards.
"""

from __future__ import annotations

from . import i18n

#: key -> {language: text}
STRINGS: dict[str, dict[str, str]] = {
    # ------------------------------------------------------------------
    # Node editor Add menu
    # ------------------------------------------------------------------
    "goo.menu.add_submenu": {
        "en_US": "GOO Engine",
        "zh_CN": "GOO 引擎",
    },
    "goo.menu.empty": {
        "en_US": "No GOO node groups are registered",
        "zh_CN": "尚未注册任何 GOO 节点组",
    },
    "goo.menu.rebuild": {
        "en_US": "Rebuild GOO Library",
        "zh_CN": "重建 GOO 节点库",
    },
    "goo.menu.add_node_group": {
        "en_US": "Add GOO Node Group",
        "zh_CN": "添加 GOO 节点组",
    },
    "goo.menu.add_node_group.desc": {
        "en_US": "Add a generated GOO node group to the edited node tree",
        "zh_CN": "将生成的 GOO 节点组添加到当前编辑的节点树",
    },
    "goo.menu.unknown_group": {
        "en_US": "Unknown GOO node group: {name}",
        "zh_CN": "未知的 GOO 节点组：{name}",
    },
    "goo.menu.no_tree": {
        "en_US": "No editable {tree_type} node tree in this context",
        "zh_CN": "当前上下文没有可编辑的 {tree_type} 节点树",
    },
    "goo.menu.added": {
        "en_US": "Added {name}",
        "zh_CN": "已添加 {name}",
    },
    # ------------------------------------------------------------------
    # Node library panel
    # ------------------------------------------------------------------
    "goo.ui.node_library": {
        "en_US": "GOO Node Library",
        "zh_CN": "GOO 节点库",
    },
    "goo.ui.node_library_count": {
        "en_US": "{count} / {total} node groups for this editor",
        "zh_CN": "此编辑器可用 {count} / {total} 个节点组",
    },
    "goo.ui.node_library_empty": {
        "en_US": "No node groups registered yet — press Rebuild Library.",
        "zh_CN": "尚未注册节点组——请点击“重建节点库”。",
    },
    "goo.ui.rebuild": {
        "en_US": "Rebuild Library",
        "zh_CN": "重建节点库",
    },
    "goo.ui.rebuild_tip": {
        "en_US": "Create or refresh every GOO node group in this file",
        "zh_CN": "在本文件中创建或刷新全部 GOO 节点组",
    },
    "goo.ui.language_hint": {
        "en_US": "Language: {language} — change it in Preferences > Add-ons > GOO NPR",
        "zh_CN": "语言：{language} — 可在 偏好设置 > 插件 > GOO NPR 中修改",
    },
    "goo.ui.language_name_en": {
        "en_US": "English",
        "zh_CN": "English",
    },
    "goo.ui.language_name_zh": {
        "en_US": "简体中文",
        "zh_CN": "简体中文",
    },
    # ------------------------------------------------------------------
    # 3D Viewport tools panel
    # ------------------------------------------------------------------
    "goo.ui.tools": {
        "en_US": "GOO Tools",
        "zh_CN": "GOO 工具",
    },
    "goo.ui.section_gp": {
        "en_US": "Grease Pencil (GP v3)",
        "zh_CN": "蜡笔（GP v3）",
    },
    "goo.ui.section_fcurve": {
        "en_US": "F-Curves",
        "zh_CN": "F 曲线",
    },
    "goo.ui.section_viewport": {
        "en_US": "Viewport",
        "zh_CN": "视图",
    },
    "goo.ui.gp_auto_close": {
        "en_US": "Auto Close Strokes",
        "zh_CN": "自动闭合笔划",
    },
    "goo.ui.gp_random_texture": {
        "en_US": "Randomize Stroke Texture",
        "zh_CN": "随机笔划纹理",
    },
    "goo.ui.gp_smooth": {
        "en_US": "Smooth Selected Strokes",
        "zh_CN": "平滑选中笔划",
    },
    "goo.ui.fcurve_isolate": {
        "en_US": "Isolate F-Curves",
        "zh_CN": "隔离 F 曲线",
    },
    "goo.ui.fcurve_restore": {
        "en_US": "Restore F-Curves",
        "zh_CN": "恢复 F 曲线",
    },
    "goo.ui.fcurve_hint": {
        "en_US": "Select curves in the Graph Editor, then Isolate.",
        "zh_CN": "在曲线编辑器中选中曲线，然后点击“隔离”。",
    },
    "goo.ui.fcurve_is_active": {
        "en_US": "Isolation active — press Restore",
        "zh_CN": "隔离已启用——点击“恢复”",
    },
    "goo.ui.linewidth_enabled": {
        "en_US": "Custom Line Width",
        "zh_CN": "自定义线宽",
    },
    "goo.ui.linewidth_value": {
        "en_US": "Line Width",
        "zh_CN": "线宽",
    },
    "goo.ui.linewidth_toggle": {
        "en_US": "Toggle Line Width",
        "zh_CN": "切换线宽",
    },
    "goo.ui.linewidth_unavailable": {
        "en_US": "Line width tools are not registered",
        "zh_CN": "线宽工具尚未注册",
    },
    "goo.ui.active_object": {
        "en_US": "Active: {name} ({type})",
        "zh_CN": "活动对象：{name}（{type}）",
    },
    "goo.ui.active_object_none": {
        "en_US": "No active object",
        "zh_CN": "没有活动对象",
    },
    "goo.ui.no_gp": {
        "en_US": "Select a Grease Pencil object",
        "zh_CN": "请选择蜡笔对象",
    },
    "goo.ui.no_action": {
        "en_US": "Active object has no Action",
        "zh_CN": "活动对象没有动作",
    },
    "goo.ui.graph_isolate": {
        "en_US": "Isolate",
        "zh_CN": "隔离",
    },
    "goo.ui.graph_restore": {
        "en_US": "Restore",
        "zh_CN": "恢复",
    },
    # ------------------------------------------------------------------
    # Grease Pencil auto close
    # ------------------------------------------------------------------
    "goo.op.gp_auto_close.label": {
        "en_US": "Auto Close GP Strokes",
        "zh_CN": "自动闭合蜡笔笔划",
    },
    "goo.op.gp_auto_close.done": {
        "en_US": "Closed {count} of {examined} stroke(s)",
        "zh_CN": "已闭合 {count} / {examined} 条笔划",
    },
    "goo.op.gp_auto_close.none": {
        "en_US": "Checked {examined} stroke(s); nothing to close",
        "zh_CN": "已检查 {examined} 条笔划，无需闭合",
    },
    "goo.op.gp_auto_close.no_gp": {
        "en_US": "No active Grease Pencil object",
        "zh_CN": "没有活动的蜡笔对象",
    },
    "goo.op.gp_auto_close.empty": {
        "en_US": "No editable Grease Pencil drawing on the visible layers",
        "zh_CN": "可见图层上没有可编辑的蜡笔画稿",
    },
    # ------------------------------------------------------------------
    # Grease Pencil random texture
    # ------------------------------------------------------------------
    "goo.op.gp_random_texture.label": {
        "en_US": "Randomize GP Stroke Texture",
        "zh_CN": "随机化蜡笔笔划纹理",
    },
    "goo.op.gp_random_texture.done": {
        "en_US": (
            "Wrote '{attribute}' on {count} stroke(s). In the material use an "
            "Attribute node named '{attribute}' to drive the texture per stroke."
        ),
        "zh_CN": (
            "已为 {count} 条笔划写入“{attribute}”。在材质中使用名为“{attribute}”的"
            "属性节点，即可按笔划随机化纹理。"
        ),
    },
    "goo.op.gp_random_texture.none": {
        "en_US": "No strokes on the visible layers",
        "zh_CN": "可见图层上没有笔划",
    },
    "goo.op.gp_random_texture.no_gp": {
        "en_US": "No active Grease Pencil object",
        "zh_CN": "没有活动的蜡笔对象",
    },
    # ------------------------------------------------------------------
    # Grease Pencil smoothing
    # ------------------------------------------------------------------
    "goo.op.gp_smooth.label": {
        "en_US": "Smooth GP Strokes",
        "zh_CN": "平滑蜡笔笔划",
    },
    "goo.op.gp_smooth.done": {
        "en_US": "Smoothed {count} stroke(s) with {iterations} pass(es)",
        "zh_CN": "已平滑 {count} 条笔划，共 {iterations} 轮",
    },
    "goo.op.gp_smooth.none": {
        "en_US": "No selected strokes with at least 3 points to smooth",
        "zh_CN": "没有可平滑的选中笔划（至少需要 3 个点）",
    },
    "goo.op.gp_smooth.no_gp": {
        "en_US": "No active Grease Pencil object",
        "zh_CN": "没有活动的蜡笔对象",
    },
    # ------------------------------------------------------------------
    # shared Grease Pencil operator properties
    # ------------------------------------------------------------------
    "goo.prop.threshold": {
        "en_US": "Gap Threshold",
        "zh_CN": "间距阈值",
    },
    "goo.prop.threshold.desc": {
        "en_US": "Close a stroke when the distance between its first and last point is below this value (object units)",
        "zh_CN": "当笔划首尾点的距离小于该值时闭合笔划（对象单位）",
    },
    "goo.prop.factor": {
        "en_US": "Smoothing Factor",
        "zh_CN": "平滑系数",
    },
    "goo.prop.factor.desc": {
        "en_US": "Weight of the neighbour average applied to every interior point",
        "zh_CN": "每个内部点向相邻点平均值靠拢的权重",
    },
    "goo.prop.iterations": {
        "en_US": "Iterations",
        "zh_CN": "迭代次数",
    },
    "goo.prop.iterations.desc": {
        "en_US": "Number of smoothing passes over the strokes",
        "zh_CN": "笔划平滑的重复轮数",
    },
    "goo.prop.only_selected": {
        "en_US": "Selected Strokes Only",
        "zh_CN": "仅选中笔划",
    },
    "goo.prop.only_selected.desc": {
        "en_US": "Only process strokes that are selected in the Grease Pencil object",
        "zh_CN": "仅处理蜡笔对象中已选中的笔划",
    },
    "goo.prop.visible_layers": {
        "en_US": "Visible Layers Only",
        "zh_CN": "仅可见图层",
    },
    "goo.prop.visible_layers.desc": {
        "en_US": "Skip hidden Grease Pencil layers",
        "zh_CN": "跳过已隐藏的蜡笔图层",
    },
    "goo.prop.selected_layers_only": {
        "en_US": "Selected Layers Only",
        "zh_CN": "仅选中图层",
    },
    "goo.prop.selected_layers_only.desc": {
        "en_US": "Only process layers selected in the Grease Pencil data",
        "zh_CN": "仅处理蜡笔数据中已选中的图层",
    },
    "goo.prop.seed": {
        "en_US": "Random Seed",
        "zh_CN": "随机种子",
    },
    "goo.prop.seed.desc": {
        "en_US": "Random seed; -1 uses a fresh random sequence",
        "zh_CN": "随机种子；-1 表示使用新的随机序列",
    },
    # ------------------------------------------------------------------
    # F-Curve isolate / restore
    # ------------------------------------------------------------------
    "goo.op.fcurve_isolate.label": {
        "en_US": "Isolate F-Curves",
        "zh_CN": "隔离 F 曲线",
    },
    "goo.op.fcurve_isolate.done": {
        "en_US": "Isolated {count} F-Curve(s); muted {muted}",
        "zh_CN": "已隔离 {count} 条 F 曲线，静音 {muted} 条",
    },
    "goo.op.fcurve_isolate.restored": {
        "en_US": "Isolation toggled off: restored {count} F-Curve(s)",
        "zh_CN": "已取消隔离：恢复 {count} 条 F 曲线",
    },
    "goo.op.fcurve_isolate.no_selection": {
        "en_US": "Select at least one F-Curve in the Graph Editor first",
        "zh_CN": "请先在曲线编辑器中至少选中一条 F 曲线",
    },
    "goo.op.fcurve_isolate.no_action": {
        "en_US": "Active object has no Action",
        "zh_CN": "活动对象没有动作",
    },
    "goo.op.fcurve_restore.label": {
        "en_US": "Restore F-Curves",
        "zh_CN": "恢复 F 曲线",
    },
    "goo.op.fcurve_restore.done": {
        "en_US": "Restored {count} of {stored} F-Curve(s)",
        "zh_CN": "已恢复 {count} / {stored} 条 F 曲线",
    },
    "goo.op.fcurve_restore.no_state": {
        "en_US": "No isolated F-Curve state in this scene",
        "zh_CN": "此场景没有隔离状态记录",
    },
    "goo.op.fcurve_restore.unknown_action": {
        "en_US": "Action '{name}' was not found",
        "zh_CN": "未找到动作“{name}”",
    },
    # ------------------------------------------------------------------
    # Viewport line width
    # ------------------------------------------------------------------
    "goo.op.viewport_linewidth_toggle.label": {
        "en_US": "Toggle Custom Line Width",
        "zh_CN": "切换自定义线宽",
    },
    "goo.op.viewport_linewidth_toggle.on": {
        "en_US": "Viewport line width enabled ({width:.2f})",
        "zh_CN": "视图线宽已启用（{width:.2f}）",
    },
    "goo.op.viewport_linewidth_toggle.off": {
        "en_US": "Viewport line width disabled",
        "zh_CN": "视图线宽已关闭",
    },
    "goo.prop.linewidth": {
        "en_US": "Viewport Line Width",
        "zh_CN": "视图线宽",
    },
    "goo.prop.linewidth.desc": {
        "en_US": "Line width used by the GOO viewport overlay (0.5 - 5.0)",
        "zh_CN": "GOO 视图叠加层使用的线宽（0.5 - 5.0）",
    },
    "goo.prop.linewidth_enabled": {
        "en_US": "Custom Viewport Line Width",
        "zh_CN": "自定义视图线宽",
    },
    "goo.prop.linewidth_enabled.desc": {
        "en_US": "Draw viewport lines with the custom width below",
        "zh_CN": "使用下方自定义宽度绘制视图线条",
    },
    # ------------------------------------------------------------------
    # Pose / rigging tools (GOO's isolate bones, Invert All, merge constraints)
    # ------------------------------------------------------------------
    "goo.pose.panel": {"en_US": "GOO Pose Tools", "zh_CN": "GOO 姿态工具"},
    "goo.pose.isolate": {"en_US": "Isolate Bones", "zh_CN": "隔离骨骼"},
    "goo.pose.isolate.desc": {
        "en_US": "Hide unselected bones; run again to restore (approximates GOO's viewport isolate flag)",
        "zh_CN": "隐藏未选中的骨骼，再次运行恢复（近似 GOO 的视口隔离开关）",
    },
    "goo.pose.isolate.button": {
        "en_US": "Isolate / Restore Bones",
        "zh_CN": "隔离 / 恢复骨骼",
    },
    "goo.pose.copy_rotation": {"en_US": "Copy Rotation", "zh_CN": "复制旋转"},
    "goo.pose.invert_all": {"en_US": "Copy Rotation: Invert All", "zh_CN": "复制旋转：全部反向"},
    "goo.pose.invert_all.desc": {
        "en_US": "Set the three per-axis invert booleans (GOO has one Invert All flag)",
        "zh_CN": "设置三个轴向反向开关（GOO 为单个全部反向标志）",
    },
    "goo.pose.outliner_box": {"en_US": "Outliner Flag", "zh_CN": "大纲视图标记"},
    "goo.pose.outliner_flag": {"en_US": "Hide in Outliner", "zh_CN": "在大纲视图中隐藏"},
    "goo.pose.outliner_flag.desc": {
        "en_US": "Set GOO's PoseBone.hide_outliner flag on selected bones; Blender 5.2 has no Outliner row filter hook, so the flag is metadata",
        "zh_CN": "给选中骨骼设置 GOO 的 PoseBone.hide_outliner 标记；Blender 5.2 没有大纲视图行过滤钩子，因此该标记仅作为元数据",
    },
    "goo.pose.outliner_hide": {"en_US": "Hide", "zh_CN": "隐藏"},
    "goo.pose.outliner_show": {"en_US": "Show", "zh_CN": "显示"},
    "goo.pose.merge_box": {"en_US": "Merge Constraints", "zh_CN": "合并约束"},
    "goo.pose.merge": {"en_US": "Merge Bone Constraints", "zh_CN": "合并骨骼约束"},
    "goo.pose.merge.desc": {
        "en_US": "Copy pose constraints from another armature (same bone names), re-linking targets to this rig",
        "zh_CN": "从另一个骨架按同名骨骼复制姿态约束，并把目标重链接到当前骨架",
    },
    "goo.pose.merge.button": {"en_US": "Merge from Source", "zh_CN": "从来源合并"},
    "goo.pose.merge_source": {"en_US": "Merge Source", "zh_CN": "合并来源"},
}


def tr(key: str, fallback: str) -> str:
    """Translate ``key``, falling back to ``fallback`` when it is unknown.

    Useful for ``bl_label`` / ``bpy.props`` names that are evaluated at class
    definition time, where a missing key would otherwise leak the raw key.
    """
    text = i18n.t(key)
    return text if text != key else fallback


def register() -> None:
    """Merge the table into the global i18n store (idempotent)."""
    i18n.register(STRINGS)


def unregister() -> None:
    """Strings are additive; leaving them registered is harmless."""
    return None


# Register immediately so modules imported after this one can translate at
# class definition time (the add-on imports this module before the UI layers).
register()

__all__ = ["STRINGS", "register", "unregister", "tr"]
