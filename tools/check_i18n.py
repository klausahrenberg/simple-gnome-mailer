"""Findet Stellen, an denen `_` (gettext) durch eine lokale Variable/Parameter verdeckt wird, aber `_()` aufgerufen wird."""
import ast
import glob
import sys

bad = 0
for path in sorted(glob.glob("postfach/**/*.py", recursive=True)):
    tree = ast.parse(open(path).read())
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.Lambda, ast.ListComp, ast.GeneratorExp, ast.SetComp, ast.DictComp)):
            continue
        binds = set()
        if isinstance(node, (ast.FunctionDef, ast.Lambda)):
            a = node.args
            binds |= {x.arg for x in a.args + a.kwonlyargs + a.posonlyargs} | ({a.vararg.arg} if a.vararg else set()) | ({a.kwarg.arg} if a.kwarg else set())
        for n in ast.walk(node):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                binds.add(n.id)
            if isinstance(n, ast.ExceptHandler) and n.name:
                binds.add(n.name)
        if "_" not in binds:
            continue
        calls = [c for c in ast.walk(node) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id == "_"]
        if calls:
            bad += 1
            print(f"{path}:{calls[0].lineno}: `_` wird in {getattr(node, 'name', 'lambda')} (Zeile {node.lineno}) verdeckt")
print("OK – keine verdeckten _()-Aufrufe" if not bad else f"{bad} Problem(e)")
sys.exit(1 if bad else 0)
