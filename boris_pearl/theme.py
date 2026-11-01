# SPDX-License-Identifier: Apache-2.0
"""One palette for every window, 60/30/10: neutral white, graphite, a single teal accent."""
from tkinter import ttk

WHITE="#f7f6f3";SURFACE="#ffffff";LINE="#dedcd6";SOFT="#ecebe6"
GRAPHITE="#2c2e33";GRAPHITE_LIGHT="#3b3e45";ON_GRAPHITE="#f4f3f0";ON_GRAPHITE_MUTED="#b9bbc0"
TEXT="#22242a";MUTED="#62656d";DISABLED="#a3a5aa"
ACCENT="#0b7f7b";ACCENT_ACTIVE="#0a6d6a";ACCENT_ON_GRAPHITE="#5fd0c9";ACCENT_SOFT="#d8ecea"


def apply(root):
    root.configure(bg=WHITE)
    style=ttk.Style(root);style.theme_use("clam")
    style.configure("TFrame",background=WHITE)
    style.configure("TLabel",background=WHITE,foreground=TEXT,font=("Segoe UI",10))
    style.configure("Muted.TLabel",foreground=MUTED);style.configure("Title.TLabel",font=("Segoe UI",23,"bold"))
    style.configure("TButton",font=("Segoe UI",10),padding=(12,8),background=SOFT,foreground=TEXT,borderwidth=0)
    style.map("TButton",background=[("disabled",WHITE),("active",LINE)],foreground=[("disabled",DISABLED)])
    style.configure("Primary.TButton",background=ACCENT,foreground="#ffffff",font=("Segoe UI",10,"bold"))
    style.map("Primary.TButton",background=[("disabled",SOFT),("active",ACCENT_ACTIVE)],foreground=[("disabled",DISABLED)])
    style.configure("Stop.TButton",background=GRAPHITE,foreground=ON_GRAPHITE,font=("Segoe UI",10,"bold"))
    style.map("Stop.TButton",background=[("active",GRAPHITE_LIGHT)])
    style.configure("TCheckbutton",background=WHITE,foreground=TEXT,font=("Segoe UI",10))
    style.map("TCheckbutton",background=[("active",WHITE)])
    style.configure("Treeview",background=SURFACE,fieldbackground=SURFACE,foreground=TEXT,rowheight=42,font=("Segoe UI",10),borderwidth=0)
    style.layout("Treeview",[("Treeview.treearea",{"sticky":"nswe"})])
    style.configure("Treeview.Heading",background=SOFT,foreground=MUTED,font=("Segoe UI",9,"bold"),padding=8,relief="flat",borderwidth=0)
    style.map("Treeview.Heading",background=[("active",SOFT)])
    style.configure("Vertical.TScrollbar",background=SOFT,troughcolor=WHITE,bordercolor=WHITE,lightcolor=SOFT,darkcolor=SOFT,arrowcolor=MUTED,arrowsize=12,relief="flat")
    style.map("Vertical.TScrollbar",background=[("active",LINE)])
    style.map("Treeview",background=[("selected",ACCENT_SOFT)],foreground=[("selected",TEXT)])
    style.configure("TEntry",fieldbackground=SURFACE,foreground=TEXT,padding=8,insertcolor=TEXT,bordercolor=LINE)
    style.configure("TCombobox",fieldbackground=SURFACE,background=SOFT,foreground=TEXT,padding=6,arrowsize=14,bordercolor=LINE)
    style.map("TCombobox",fieldbackground=[("readonly",SURFACE)],selectbackground=[("readonly",SURFACE)],selectforeground=[("readonly",TEXT)],foreground=[("readonly",TEXT)])
    root.option_add("*TCombobox*Listbox.background",SURFACE);root.option_add("*TCombobox*Listbox.foreground",TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground",ACCENT_SOFT);root.option_add("*TCombobox*Listbox.selectForeground",TEXT)
