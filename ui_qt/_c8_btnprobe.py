# -*- coding: utf-8 -*-
"""丙-8 P0-A② 取证：ui_qt 全量按钮接线扫描（AST 级）。

用户原话「点好多按钮没反应」⇒ 逐个走查的第一步是**机械穷举**：
每个 Btn/IconBtn/QPushButton 创建点，是否在同作用域内有 clicked.connect。
 Known 统一接线（脚本标注不算缺失）：
  · _btn_row(t, pairs, hooks)：按钮数组统一 connect hooks[i]
  · Segmented/Combo 等非按钮控件不在扫描范围
"""
import ast
from pathlib import Path

HERE = Path(__file__).resolve().parent
BTN_TYPES = {"Btn", "IconBtn", "QPushButton", "ToolButton"}
out: list[str] = []


def scan(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    rel = path.name

    def walk(fn: ast.AST, ctx: str) -> None:
        created: dict[str, int] = {}       # varname -> lineno
        connected: set[str] = set()
        hooks_lists: list[str] = []        # 传给 _btn_row 的 hooks 变量
        for node in ast.walk(fn):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node is not fn:
                continue  # walk 已递归嵌套；这里不需要防重
            if isinstance(node, ast.Assign):
                v = node.value
                if isinstance(v, ast.Call) and isinstance(v.func, ast.Name) and v.func.id in BTN_TYPES:
                    for tgt in node.targets:
                        if isinstance(tgt, ast.Name):
                            created[tgt.id] = node.lineno
            if isinstance(node, ast.Call):
                f = node.func
                #  x.clicked.connect(...)
                if isinstance(f, ast.Attribute) and f.attr == "connect" \
                        and isinstance(f.value, ast.Attribute) and f.value.attr == "clicked" \
                        and isinstance(f.value.value, ast.Name):
                    connected.add(f.value.value.id)
                # _btn_row(t, pairs, hooks) —— hooks 参数记名
                if isinstance(f, ast.Name) and f.id == "_btn_row" and node.args:
                    for a in node.args[1:]:
                        if isinstance(a, ast.Name):
                            hooks_lists.append(a.id)
                    for kw in node.keywords:
                        if kw.arg == "hooks" and isinstance(kw.value, ast.Name):
                            hooks_lists.append(kw.value.id)
        for name, ln in sorted(created.items(), key=lambda x: x[1]):
            if name not in connected:
                out.append(f"{rel}:{ln}  [{ctx}]  {name} = {BTN_TYPES & set()}创建后无 clicked.connect")

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            walk(node, f"def {node.name}")
        elif isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    walk(sub, f"class {node.name}.{sub.name}")
        else:
            walk(node, "module")


for p in sorted(HERE.glob("*.py")):
    if p.name.startswith("_c8"):
        continue
    try:
        scan(p)
    except SyntaxError as e:
        out.append(f"{p.name}: SYNTAX ERROR {e}")

out.append("")
out.append("（说明： hooks 数组式接线（_btn_row）由调用点的 hooks 列表决定，")
out.append("  扫描只标「散装创建未连接」；_btn_row 调用点的 hooks=[..., None] 缺口另行核对。）")

(HERE / "_c8_btnprobe_out.log").write_text("\n".join(out), encoding="utf-8")
print("written", len(out), "lines")
