#!/usr/bin/env python3
"""Local GUI smoke; constructs a hidden window and invokes only offline selftest."""
from pathlib import Path
import sys
import tkinter as tk

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from boris_pearl import __main__ as ui

original = tk.Tk
result = []


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def factory():
    root = original()
    root.withdraw()
    def inspect():
        try:
            buttons = [w for w in descendants(root) if w.winfo_class() == "TButton"]
            start = [w for w in buttons if w.cget("text") == "Начать майнинг"][0]
            assert start.instate(["disabled"]), "Live start must remain disabled"
            check = [w for w in buttons if "локальным" in w.cget("text")][0]
            check.invoke()
            content = [w for w in descendants(root) if w.winfo_class() == "Text"][0].get("1.0", "end")
            assert '"proof_matches": true' in content
            result.append("PASS: full GUI constructed, live start disabled, local synthetic worker button succeeded")
        except Exception as exc:
            result.append("FAIL: " + str(exc))
        finally:
            root.quit()
    root.after(0, inspect)
    return root


tk.Tk = factory
ui.gui()
if tk._default_root:
    tk._default_root.destroy()
print("\n".join(result))
raise SystemExit(0 if result and result[0].startswith("PASS") else 1)
