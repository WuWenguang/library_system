from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any, Optional

from .db import DEFAULT_DB_PATH, Database
from .services import BOOK_FIELDS, LibraryError, LibraryService, clean_text


REASON_LABELS = {
    "stock_adjust": "库存/图书修改",
    "renewal": "续借原因",
    "lost_damage": "丢失报损原因",
}
REASON_BY_LABEL = {label: key for key, label in REASON_LABELS.items()}
APP_VERSION = "0.2.0"


def startup_log(message: str) -> None:
    print(f"[图书管理系统] {message}", flush=True)


def clear_tree(tree: ttk.Treeview) -> None:
    for item in tree.get_children():
        tree.delete(item)


def tree_selection_values(tree: ttk.Treeview) -> Optional[tuple[Any, ...]]:
    selected = tree.selection()
    if not selected:
        return None
    return tree.item(selected[0], "values")


class ReasonDialog(simpledialog.Dialog):
    def __init__(
        self,
        parent: tk.Misc,
        title: str,
        reasons: list[dict[str, Any]],
        include_days: bool = False,
        initial_days: int = 15,
    ) -> None:
        self.reasons = reasons
        self.include_days = include_days
        self.initial_days = initial_days
        self.reason_var = tk.StringVar()
        self.days_var = tk.StringVar(value=str(initial_days))
        self.note_var = tk.StringVar()
        self.result_data: Optional[dict[str, Any]] = None
        super().__init__(parent, title)

    def body(self, master: tk.Misc) -> tk.Widget:
        ttk.Label(master, text="原因").grid(row=0, column=0, sticky="w", padx=6, pady=6)
        names = [reason["name"] for reason in self.reasons]
        if names:
            self.reason_var.set(names[0])
        combo = ttk.Combobox(master, textvariable=self.reason_var, values=names, state="readonly", width=24)
        combo.grid(row=0, column=1, sticky="ew", padx=6, pady=6)
        row = 1
        if self.include_days:
            ttk.Label(master, text="天数").grid(row=row, column=0, sticky="w", padx=6, pady=6)
            ttk.Entry(master, textvariable=self.days_var, width=10).grid(row=row, column=1, sticky="w", padx=6, pady=6)
            row += 1
        ttk.Label(master, text="备注").grid(row=row, column=0, sticky="w", padx=6, pady=6)
        ttk.Entry(master, textvariable=self.note_var, width=32).grid(row=row, column=1, sticky="ew", padx=6, pady=6)
        return combo

    def validate(self) -> bool:
        if not self.reason_var.get():
            messagebox.showwarning("提示", "请选择原因", parent=self)
            return False
        if self.include_days:
            try:
                if int(self.days_var.get()) <= 0:
                    raise ValueError
            except ValueError:
                messagebox.showwarning("提示", "天数必须是大于 0 的整数", parent=self)
                return False
        return True

    def apply(self) -> None:
        reason_name = self.reason_var.get()
        reason = next(item for item in self.reasons if item["name"] == reason_name)
        self.result_data = {
            "reason_id": reason["id"],
            "days": int(self.days_var.get()) if self.include_days else None,
            "note": self.note_var.get(),
        }


class LibraryApp(tk.Tk):
    def __init__(self, db_path: str = str(DEFAULT_DB_PATH)) -> None:
        startup_log("开始创建窗口")
        super().__init__()
        self.title(f"图书借阅管理系统 v{APP_VERSION}")
        self.geometry("1220x780")
        self.minsize(1040, 680)
        self.configure(bg="#f3f4f6")
        self.option_add("*Foreground", "#111827")
        self.option_add("*Background", "#f3f4f6")
        self.option_add("*insertBackground", "#111827")
        self.db_path = db_path
        self.create_boot_banner()
        self.after(100, self.finish_startup)
        startup_log("窗口已创建，等待界面初始化")

    def finish_startup(self) -> None:
        try:
            self._finish_startup()
        except tk.TclError as exc:
            startup_log(f"窗口已关闭，停止启动：{exc}")
        except Exception as exc:
            startup_log(f"启动失败：{exc}")
            messagebox.showerror("启动失败", str(exc), parent=self)

    def _finish_startup(self) -> None:
        startup_log("开始初始化数据库")
        self.service = LibraryService(Database(self.db_path))
        startup_log("数据库初始化完成")
        self.shelves: list[dict[str, Any]] = []
        self.shelf_name_to_id: dict[str, int] = {}
        self.reasons_by_category: dict[str, list[dict[str, Any]]] = {}
        self.current_batch_id: Optional[int] = None
        self.inventory_selected: Optional[dict[str, Any]] = None
        self.reader_selected_id: Optional[int] = None
        self.borrow_reader_id: Optional[int] = None
        self.return_reader_id: Optional[int] = None
        self.borrow_barcodes: list[str] = []
        self.return_barcodes: list[str] = []

        self.style = ttk.Style(self)
        self.configure_theme()

        startup_log("开始构建界面")
        self.refresh_lookups()
        if self.boot_banner.winfo_exists():
            self.boot_banner.destroy()
        header = tk.Frame(self, bg="#1f2937", height=50)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(
            header,
            text="图书借阅管理系统",
            bg="#1f2937",
            fg="#ffffff",
            font=("Arial", 18, "bold"),
        ).pack(side="left", padx=18)
        tk.Label(
            header,
            text="本地桌面版",
            bg="#1f2937",
            fg="#cbd5e1",
            font=("Arial", 12),
        ).pack(side="left", padx=8)
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True)
        self.build_home_tab()
        self.build_inbound_tab()
        self.build_inventory_tab()
        self.build_readers_tab()
        self.build_borrow_tab()
        self.build_return_tab()
        self.build_settings_tab()
        self.refresh_all()
        self.update_idletasks()
        startup_log("界面构建完成")

    def create_boot_banner(self) -> None:
        self.boot_banner = tk.Canvas(self, bg="#ffffff", highlightthickness=0, height=130)
        self.boot_banner.pack(fill="x", padx=0, pady=0)
        self.boot_banner.create_rectangle(0, 0, 2000, 130, fill="#ffffff", outline="")
        self.boot_banner.create_rectangle(0, 0, 2000, 18, fill="#2563eb", outline="")
        self.boot_banner.create_text(
            24,
            52,
            text="Library System / 图书借阅管理系统",
            anchor="w",
            fill="#111827",
            font=("Arial", 24, "bold"),
        )
        self.boot_banner.create_text(
            24,
            92,
            text="正在启动，请稍候...  如果只看到空白窗口，请查看终端中的启动日志。",
            anchor="w",
            fill="#374151",
            font=("Arial", 14),
        )

    def configure_theme(self) -> None:
        try:
            self.style.theme_use("clam")
        except tk.TclError:
            pass
        background = "#f3f4f6"
        surface = "#ffffff"
        text = "#111827"
        muted = "#4b5563"
        border = "#d1d5db"
        selected = "#2563eb"
        self.style.configure(".", background=background, foreground=text, font=("Arial", 12))
        self.style.configure("TFrame", background=background)
        self.style.configure("TLabelframe", background=background, bordercolor=border)
        self.style.configure("TLabelframe.Label", background=background, foreground=text)
        self.style.configure("TLabel", background=background, foreground=text)
        self.style.configure("Title.TLabel", background=background, foreground=text, font=("Arial", 18, "bold"))
        self.style.configure("Hint.TLabel", background=background, foreground=muted)
        self.style.configure("TButton", padding=(10, 5), background="#e5e7eb", foreground=text)
        self.style.map("TButton", background=[("active", "#d1d5db")])
        self.style.configure("TEntry", fieldbackground=surface, foreground=text, insertcolor=text)
        self.style.configure("TCombobox", fieldbackground=surface, foreground=text, arrowcolor=text)
        self.style.configure("TNotebook", background=background, borderwidth=0)
        self.style.configure(
            "TNotebook.Tab",
            background="#e5e7eb",
            foreground=text,
            padding=(16, 8),
            font=("Arial", 12, "bold"),
        )
        self.style.map(
            "TNotebook.Tab",
            background=[("selected", surface), ("active", "#f9fafb")],
            foreground=[("selected", selected), ("active", text)],
        )
        self.style.configure(
            "Treeview",
            background=surface,
            fieldbackground=surface,
            foreground=text,
            rowheight=26,
            bordercolor=border,
        )
        self.style.configure(
            "Treeview.Heading",
            background="#e5e7eb",
            foreground=text,
            font=("Arial", 12, "bold"),
        )
        self.style.map("Treeview", background=[("selected", selected)], foreground=[("selected", "#ffffff")])

    def run_safe(self, action: Any, success: str = "", refresh: bool = True) -> None:
        try:
            action()
        except LibraryError as exc:
            messagebox.showwarning("提示", str(exc), parent=self)
        except Exception as exc:
            messagebox.showerror("错误", str(exc), parent=self)
        else:
            if success:
                messagebox.showinfo("完成", success, parent=self)
            if refresh:
                self.refresh_all()

    def make_tree(
        self,
        parent: tk.Misc,
        columns: list[tuple[str, str, int]],
        height: int = 12,
    ) -> ttk.Treeview:
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True)
        tree = ttk.Treeview(frame, columns=[col[0] for col in columns], show="headings", height=height)
        vsb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        hsb = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        for key, heading, width in columns:
            tree.heading(key, text=heading)
            tree.column(key, width=width, minwidth=0, stretch=width > 0)
        return tree

    def refresh_lookups(self) -> None:
        self.shelves = self.service.list_shelves(active_only=True)
        self.shelf_name_to_id = {shelf["name"]: shelf["id"] for shelf in self.shelves}
        self.reasons_by_category = {
            category: self.service.list_reasons(category)
            for category in REASON_LABELS
        }

    def shelf_names(self, include_all: bool = False) -> list[str]:
        names = [shelf["name"] for shelf in self.shelves]
        return ["全部"] + names if include_all else names

    def selected_shelf_id(self, combo: ttk.Combobox, allow_all: bool = False) -> Optional[int]:
        name = combo.get()
        if allow_all and name == "全部":
            return None
        shelf_id = self.shelf_name_to_id.get(name)
        if shelf_id is None:
            raise LibraryError("请选择有效书架")
        return shelf_id

    def selected_reason_id(self, category: str, combo: ttk.Combobox) -> int:
        name = combo.get()
        for reason in self.reasons_by_category.get(category, []):
            if reason["name"] == name:
                return int(reason["id"])
        raise LibraryError("请选择有效原因")

    def refresh_all(self) -> None:
        self.refresh_lookups()
        for method_name in [
            "refresh_home",
            "refresh_inbound_items",
            "refresh_inventory",
            "refresh_readers",
            "refresh_borrow_reader",
            "refresh_return_reader",
            "refresh_settings",
        ]:
            if hasattr(self, method_name):
                getattr(self, method_name)()

    def build_home_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(tab, text="首页")
        ttk.Label(tab, text="图书借阅概览", style="Title.TLabel").pack(anchor="w")
        stats = ttk.Frame(tab)
        stats.pack(fill="x", pady=(10, 12))
        self.stat_labels: dict[str, ttk.Label] = {}
        for index, (key, label) in enumerate(
            [
                ("borrowed_today", "今日借出"),
                ("returned_today", "今日归还"),
                ("overdue", "逾期未还"),
                ("zero_stock", "零库存条目"),
            ]
        ):
            box = ttk.Frame(stats, padding=8, relief="ridge")
            box.grid(row=0, column=index, sticky="ew", padx=(0, 8))
            ttk.Label(box, text=label).pack(anchor="w")
            value = ttk.Label(box, text="0", font=("Arial", 18, "bold"))
            value.pack(anchor="w")
            self.stat_labels[key] = value
            stats.columnconfigure(index, weight=1)
        ttk.Label(tab, text="逾期未还读者提醒").pack(anchor="w", pady=(4, 6))
        self.overdue_tree = self.make_tree(
            tab,
            [
                ("loan_id", "loan_id", 0),
                ("loan_item_id", "item_id", 0),
                ("reader", "读者", 100),
                ("phone", "手机号", 120),
                ("department", "部门", 100),
                ("barcode", "条码", 130),
                ("title", "书名", 220),
                ("shelf", "书架", 100),
                ("due_date", "到期日", 100),
                ("days", "逾期天数", 80),
            ],
            height=18,
        )
        buttons = ttk.Frame(tab)
        buttons.pack(fill="x", pady=8)
        ttk.Button(buttons, text="刷新", command=self.refresh_home).pack(side="left")
        ttk.Button(buttons, text="续借", command=self.renew_selected_overdue).pack(side="left", padx=6)
        ttk.Button(buttons, text="丢失报损", command=self.lost_selected_overdue).pack(side="left")

    def refresh_home(self) -> None:
        if not hasattr(self, "overdue_tree"):
            return
        stats = self.service.dashboard_stats()
        for key, value in stats.items():
            self.stat_labels[key].configure(text=str(value))
        clear_tree(self.overdue_tree)
        for row in self.service.list_overdue():
            self.overdue_tree.insert(
                "",
                "end",
                values=(
                    row["loan_id"],
                    row["loan_item_id"],
                    row["reader_name"],
                    row["phone"],
                    row["department"],
                    row["barcode"],
                    row["title"],
                    row["shelf_name"],
                    row["due_date"],
                    row["days_overdue"],
                ),
            )

    def renew_selected_overdue(self) -> None:
        values = tree_selection_values(self.overdue_tree)
        if not values:
            messagebox.showwarning("提示", "请选择逾期记录", parent=self)
            return
        reasons = self.reasons_by_category["renewal"]
        dialog = ReasonDialog(self, "续借", reasons, include_days=True, initial_days=self.service.default_renewal_days())
        if not dialog.result_data:
            return

        def action() -> None:
            self.service.renew_loan(
                int(values[0]),
                dialog.result_data["days"],
                dialog.result_data["reason_id"],
                dialog.result_data["note"],
            )

        self.run_safe(action, "续借已完成")

    def lost_selected_overdue(self) -> None:
        values = tree_selection_values(self.overdue_tree)
        if not values:
            messagebox.showwarning("提示", "请选择逾期记录", parent=self)
            return
        reasons = self.reasons_by_category["lost_damage"]
        dialog = ReasonDialog(self, "丢失报损", reasons)
        if not dialog.result_data:
            return
        if not messagebox.askyesno("确认", "确定将该借阅图书标记为丢失报损？", parent=self):
            return

        def action() -> None:
            self.service.mark_lost(
                int(values[1]),
                dialog.result_data["reason_id"],
                dialog.result_data["note"],
            )

        self.run_safe(action, "丢失报损已记录")

    def build_inbound_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(tab, text="图书入库")
        top = ttk.Frame(tab)
        top.pack(fill="x")
        self.batch_note_var = tk.StringVar()
        self.batch_label_var = tk.StringVar(value="当前批次：未创建")
        ttk.Label(top, textvariable=self.batch_label_var, style="Hint.TLabel").pack(side="left")
        ttk.Label(top, text="批次备注").pack(side="left", padx=(24, 4))
        ttk.Entry(top, textvariable=self.batch_note_var, width=28).pack(side="left")
        ttk.Button(top, text="新建批次", command=self.create_batch).pack(side="left", padx=6)
        ttk.Button(top, text="确认入库", command=self.confirm_batch).pack(side="right")

        form = ttk.LabelFrame(tab, text="录入流水", padding=10)
        form.pack(fill="x", pady=10)
        self.inbound_vars = {field: tk.StringVar() for field in BOOK_FIELDS}
        fields = [
            ("barcode", "条码", 18),
            ("title", "书名", 28),
            ("author", "作者", 18),
            ("publisher", "出版社", 18),
            ("isbn", "ISBN", 18),
            ("book_no", "书号", 18),
            ("category", "分类", 16),
        ]
        for index, (key, label, width) in enumerate(fields):
            ttk.Label(form, text=label).grid(row=index // 4, column=(index % 4) * 2, sticky="w", padx=4, pady=4)
            entry = ttk.Entry(form, textvariable=self.inbound_vars[key], width=width)
            entry.grid(row=index // 4, column=(index % 4) * 2 + 1, sticky="ew", padx=4, pady=4)
            if key == "barcode":
                entry.bind("<Return>", lambda event: self.lookup_inbound_barcode())
        ttk.Label(form, text="书架").grid(row=2, column=0, sticky="w", padx=4, pady=4)
        self.inbound_shelf_combo = ttk.Combobox(form, state="readonly", width=16)
        self.inbound_shelf_combo.grid(row=2, column=1, sticky="ew", padx=4, pady=4)
        ttk.Label(form, text="数量").grid(row=2, column=2, sticky="w", padx=4, pady=4)
        self.inbound_qty_var = tk.StringVar(value="1")
        ttk.Entry(form, textvariable=self.inbound_qty_var, width=10).grid(row=2, column=3, sticky="w", padx=4, pady=4)
        ttk.Label(form, text="备注").grid(row=2, column=4, sticky="w", padx=4, pady=4)
        self.inbound_note_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.inbound_note_var, width=24).grid(row=2, column=5, sticky="ew", padx=4, pady=4)
        ttk.Button(form, text="查询条码", command=self.lookup_inbound_barcode).grid(row=2, column=6, padx=4)
        ttk.Button(form, text="添加流水", command=self.add_inbound_item).grid(row=2, column=7, padx=4)
        ttk.Button(form, text="修改选中", command=self.update_inbound_item).grid(row=2, column=8, padx=4)
        ttk.Button(form, text="删除选中", command=self.delete_inbound_item).grid(row=2, column=9, padx=4)

        self.inbound_tree = self.make_tree(
            tab,
            [
                ("id", "id", 0),
                ("barcode", "条码", 130),
                ("title", "书名", 220),
                ("author", "作者", 100),
                ("publisher", "出版社", 120),
                ("isbn", "ISBN", 130),
                ("book_no", "书号", 120),
                ("category", "分类", 90),
                ("shelf", "书架", 100),
                ("quantity", "数量", 70),
                ("note", "备注", 160),
            ],
            height=16,
        )
        self.inbound_tree.bind("<<TreeviewSelect>>", lambda event: self.load_inbound_selected())

    def create_batch(self) -> None:
        def action() -> None:
            self.current_batch_id = self.service.create_inbound_batch(self.batch_note_var.get())
            batch = self.service.get_batch(self.current_batch_id)
            self.batch_label_var.set(f"当前批次：{batch['code']}")

        self.run_safe(action, "新批次已创建", refresh=False)
        self.refresh_inbound_items()

    def lookup_inbound_barcode(self) -> None:
        barcode = self.inbound_vars["barcode"].get()
        book = self.service.find_book_by_barcode(barcode)
        if book:
            for field in BOOK_FIELDS:
                if field in self.inbound_vars:
                    self.inbound_vars[field].set(book.get(field, ""))
            messagebox.showinfo("条码查询", "该图书已入库，可直接录入入库数量", parent=self)
        else:
            keep = self.inbound_vars["barcode"].get()
            for field in self.inbound_vars:
                if field != "barcode":
                    self.inbound_vars[field].set("")
            self.inbound_vars["barcode"].set(keep)

    def inbound_book_data(self) -> dict[str, str]:
        return {field: self.inbound_vars[field].get() for field in self.inbound_vars}

    def add_inbound_item(self) -> None:
        def action() -> None:
            if self.current_batch_id is None:
                self.current_batch_id = self.service.create_inbound_batch(self.batch_note_var.get())
                batch = self.service.get_batch(self.current_batch_id)
                self.batch_label_var.set(f"当前批次：{batch['code']}")
            self.service.add_inbound_item(
                self.current_batch_id,
                self.inbound_book_data(),
                self.selected_shelf_id(self.inbound_shelf_combo) or 0,
                self.inbound_qty_var.get(),
                self.inbound_note_var.get(),
            )

        self.run_safe(action, refresh=False)
        self.refresh_inbound_items()

    def update_inbound_item(self) -> None:
        values = tree_selection_values(self.inbound_tree)
        if not values:
            messagebox.showwarning("提示", "请选择要修改的流水", parent=self)
            return

        def action() -> None:
            self.service.update_inbound_item(
                int(values[0]),
                self.inbound_book_data(),
                self.selected_shelf_id(self.inbound_shelf_combo) or 0,
                self.inbound_qty_var.get(),
                self.inbound_note_var.get(),
            )

        self.run_safe(action, "流水已修改", refresh=False)
        self.refresh_inbound_items()

    def delete_inbound_item(self) -> None:
        values = tree_selection_values(self.inbound_tree)
        if not values:
            messagebox.showwarning("提示", "请选择要删除的流水", parent=self)
            return
        if not messagebox.askyesno("确认", "确定删除选中的入库流水？", parent=self):
            return

        def action() -> None:
            self.service.delete_inbound_item(int(values[0]))

        self.run_safe(action, "流水已删除", refresh=False)
        self.refresh_inbound_items()

    def load_inbound_selected(self) -> None:
        values = tree_selection_values(self.inbound_tree)
        if not values:
            return
        keys = ["id", "barcode", "title", "author", "publisher", "isbn", "book_no", "category", "shelf", "quantity", "note"]
        row = dict(zip(keys, values))
        for field in ["barcode", "title", "author", "publisher", "isbn", "book_no", "category"]:
            self.inbound_vars[field].set(row[field])
        self.inbound_shelf_combo.set(row["shelf"])
        self.inbound_qty_var.set(str(row["quantity"]))
        self.inbound_note_var.set(row["note"])

    def confirm_batch(self) -> None:
        if self.current_batch_id is None:
            messagebox.showwarning("提示", "请先创建或录入入库批次", parent=self)
            return
        if not messagebox.askyesno("确认", "确认后该批次会统一写入库存，是否继续？", parent=self):
            return

        def action() -> None:
            self.service.confirm_inbound_batch(self.current_batch_id or 0)
            self.current_batch_id = None
            self.batch_label_var.set("当前批次：未创建")

        self.run_safe(action, "入库已确认")

    def refresh_inbound_items(self) -> None:
        if not hasattr(self, "inbound_tree"):
            return
        self.inbound_shelf_combo.configure(values=self.shelf_names())
        if not self.inbound_shelf_combo.get() and self.shelves:
            self.inbound_shelf_combo.set(self.shelves[0]["name"])
        clear_tree(self.inbound_tree)
        if self.current_batch_id is None:
            return
        batch = self.service.get_batch(self.current_batch_id)
        if batch:
            self.batch_label_var.set(f"当前批次：{batch['code']}")
        for row in self.service.list_inbound_items(self.current_batch_id):
            self.inbound_tree.insert(
                "",
                "end",
                values=(
                    row["id"],
                    row["barcode"],
                    row["title"],
                    row["author"],
                    row["publisher"],
                    row["isbn"],
                    row["book_no"],
                    row["category"],
                    row["shelf_name"],
                    row["quantity"],
                    row["note"],
                ),
            )

    def build_inventory_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(tab, text="图书库存/调整")
        filters = ttk.Frame(tab)
        filters.pack(fill="x")
        self.inventory_keyword_var = tk.StringVar()
        ttk.Label(filters, text="关键词").pack(side="left")
        ttk.Entry(filters, textvariable=self.inventory_keyword_var, width=28).pack(side="left", padx=6)
        ttk.Label(filters, text="书架").pack(side="left")
        self.inventory_shelf_filter = ttk.Combobox(filters, state="readonly", width=16)
        self.inventory_shelf_filter.pack(side="left", padx=6)
        ttk.Button(filters, text="查询", command=self.refresh_inventory).pack(side="left")
        ttk.Button(filters, text="导出库存 CSV", command=self.export_inventory).pack(side="right")
        self.inventory_tree = self.make_tree(
            tab,
            [
                ("book_id", "book_id", 0),
                ("shelf_id", "shelf_id", 0),
                ("barcode", "条码", 130),
                ("title", "书名", 220),
                ("author", "作者", 100),
                ("publisher", "出版社", 120),
                ("isbn", "ISBN", 130),
                ("book_no", "书号", 120),
                ("category", "分类", 90),
                ("shelf", "书架", 100),
                ("quantity", "库存", 70),
                ("price", "price", 0),
                ("publish_date", "publish_date", 0),
                ("description", "description", 0),
            ],
            height=13,
        )
        self.inventory_tree.bind("<<TreeviewSelect>>", lambda event: self.load_inventory_selected())

        editor = ttk.LabelFrame(tab, text="修改图书信息或库存", padding=10)
        editor.pack(fill="x", pady=10)
        self.inventory_vars = {field: tk.StringVar() for field in BOOK_FIELDS}
        edit_fields = [
            ("title", "书名", 28),
            ("author", "作者", 16),
            ("publisher", "出版社", 18),
            ("isbn", "ISBN", 18),
            ("book_no", "书号", 18),
            ("category", "分类", 14),
            ("price", "价格", 10),
            ("publish_date", "出版日期", 12),
        ]
        for index, (key, label, width) in enumerate(edit_fields):
            ttk.Label(editor, text=label).grid(row=index // 4, column=(index % 4) * 2, sticky="w", padx=4, pady=4)
            ttk.Entry(editor, textvariable=self.inventory_vars[key], width=width).grid(
                row=index // 4,
                column=(index % 4) * 2 + 1,
                sticky="ew",
                padx=4,
                pady=4,
            )
        ttk.Label(editor, text="库存数量").grid(row=2, column=0, sticky="w", padx=4, pady=4)
        self.inventory_qty_var = tk.StringVar()
        ttk.Entry(editor, textvariable=self.inventory_qty_var, width=10).grid(row=2, column=1, sticky="w", padx=4, pady=4)
        ttk.Label(editor, text="修改原因").grid(row=2, column=2, sticky="w", padx=4, pady=4)
        self.inventory_reason_combo = ttk.Combobox(editor, state="readonly", width=18)
        self.inventory_reason_combo.grid(row=2, column=3, sticky="w", padx=4, pady=4)
        ttk.Label(editor, text="备注").grid(row=2, column=4, sticky="w", padx=4, pady=4)
        self.inventory_note_var = tk.StringVar()
        ttk.Entry(editor, textvariable=self.inventory_note_var, width=26).grid(row=2, column=5, sticky="ew", padx=4, pady=4)
        ttk.Button(editor, text="保存修改", command=self.save_inventory_adjustment).grid(row=2, column=6, padx=8)

    def refresh_inventory(self) -> None:
        if not hasattr(self, "inventory_tree"):
            return
        self.inventory_shelf_filter.configure(values=self.shelf_names(include_all=True))
        if not self.inventory_shelf_filter.get():
            self.inventory_shelf_filter.set("全部")
        reason_names = [reason["name"] for reason in self.reasons_by_category["stock_adjust"]]
        self.inventory_reason_combo.configure(values=reason_names)
        if reason_names and not self.inventory_reason_combo.get():
            self.inventory_reason_combo.set(reason_names[0])
        clear_tree(self.inventory_tree)
        shelf_id = self.selected_shelf_id(self.inventory_shelf_filter, allow_all=True)
        for row in self.service.list_inventory(self.inventory_keyword_var.get(), shelf_id):
            self.inventory_tree.insert(
                "",
                "end",
                values=(
                    row["book_id"],
                    row["shelf_id"],
                    row["barcode"],
                    row["title"],
                    row["author"],
                    row["publisher"],
                    row["isbn"],
                    row["book_no"],
                    row["category"],
                    row["shelf_name"],
                    row["quantity"],
                    row["price"],
                    row["publish_date"],
                    row["description"],
                ),
            )

    def load_inventory_selected(self) -> None:
        values = tree_selection_values(self.inventory_tree)
        if not values:
            return
        keys = [
            "book_id",
            "shelf_id",
            "barcode",
            "title",
            "author",
            "publisher",
            "isbn",
            "book_no",
            "category",
            "shelf",
            "quantity",
            "price",
            "publish_date",
            "description",
        ]
        row = dict(zip(keys, values))
        self.inventory_selected = {
            "book_id": int(row["book_id"]),
            "shelf_id": int(row["shelf_id"]),
            "barcode": row["barcode"],
        }
        for field in self.inventory_vars:
            self.inventory_vars[field].set("")
        for field in ["barcode", "title", "author", "publisher", "isbn", "book_no", "category", "price", "publish_date", "description"]:
            self.inventory_vars[field].set(row.get(field, ""))
        self.inventory_qty_var.set(str(row["quantity"]))

    def save_inventory_adjustment(self) -> None:
        if not self.inventory_selected:
            messagebox.showwarning("提示", "请选择库存记录", parent=self)
            return

        def action() -> None:
            self.service.adjust_book_inventory(
                self.inventory_selected["book_id"],
                self.inventory_selected["shelf_id"],
                self.inventory_qty_var.get(),
                {field: self.inventory_vars[field].get() for field in self.inventory_vars},
                self.selected_reason_id("stock_adjust", self.inventory_reason_combo),
                self.inventory_note_var.get(),
            )

        self.run_safe(action, "修改已保存")

    def export_inventory(self) -> None:
        path = filedialog.asksaveasfilename(
            parent=self,
            title="导出库存",
            defaultextension=".csv",
            filetypes=[("CSV 文件", "*.csv")],
        )
        if not path:
            return

        def action() -> None:
            shelf_id = self.selected_shelf_id(self.inventory_shelf_filter, allow_all=True)
            self.service.export_inventory_csv(path, shelf_id)

        self.run_safe(action, "库存已导出", refresh=False)

    def build_readers_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(tab, text="读者管理")
        form = ttk.LabelFrame(tab, text="读者信息", padding=10)
        form.pack(fill="x")
        self.reader_vars = {
            "name": tk.StringVar(),
            "phone": tk.StringVar(),
            "department": tk.StringVar(),
            "contact": tk.StringVar(),
            "status": tk.StringVar(value="正常"),
        }
        fields = [("name", "姓名"), ("phone", "手机号"), ("department", "部门"), ("contact", "联系方式")]
        for index, (key, label) in enumerate(fields):
            ttk.Label(form, text=label).grid(row=0, column=index * 2, padx=4, pady=4, sticky="w")
            ttk.Entry(form, textvariable=self.reader_vars[key], width=18).grid(row=0, column=index * 2 + 1, padx=4, pady=4)
        ttk.Label(form, text="状态").grid(row=0, column=8, padx=4, sticky="w")
        ttk.Combobox(form, textvariable=self.reader_vars["status"], values=["正常", "停用"], width=10, state="readonly").grid(
            row=0,
            column=9,
            padx=4,
        )
        ttk.Button(form, text="新增", command=self.new_reader).grid(row=0, column=10, padx=4)
        ttk.Button(form, text="保存", command=self.save_reader).grid(row=0, column=11, padx=4)

        search = ttk.Frame(tab)
        search.pack(fill="x", pady=8)
        self.reader_search_var = tk.StringVar()
        ttk.Label(search, text="查询").pack(side="left")
        ttk.Entry(search, textvariable=self.reader_search_var, width=30).pack(side="left", padx=6)
        ttk.Button(search, text="搜索", command=self.refresh_readers).pack(side="left")

        body = ttk.PanedWindow(tab, orient="vertical")
        body.pack(fill="both", expand=True)
        readers_frame = ttk.Frame(body)
        loans_frame = ttk.Frame(body)
        body.add(readers_frame, weight=2)
        body.add(loans_frame, weight=2)
        self.readers_tree = self.make_tree(
            readers_frame,
            [
                ("id", "id", 0),
                ("name", "姓名", 120),
                ("phone", "手机号", 130),
                ("department", "部门", 120),
                ("contact", "联系方式", 160),
                ("status", "状态", 80),
            ],
            height=8,
        )
        self.readers_tree.bind("<<TreeviewSelect>>", lambda event: self.load_reader_selected())
        ttk.Label(loans_frame, text="当前在借").pack(anchor="w", pady=(6, 2))
        self.reader_loans_tree = self.make_tree(
            loans_frame,
            [
                ("loan_item_id", "item_id", 0),
                ("barcode", "条码", 130),
                ("title", "书名", 220),
                ("shelf", "书架", 100),
                ("borrowed_at", "借出时间", 150),
                ("due_date", "到期日", 100),
            ],
            height=7,
        )
        ttk.Label(loans_frame, text="借阅历史").pack(anchor="w", pady=(8, 2))
        self.reader_history_tree = self.make_tree(
            loans_frame,
            [
                ("loan_item_id", "item_id", 0),
                ("barcode", "条码", 130),
                ("title", "书名", 220),
                ("shelf", "书架", 100),
                ("borrowed_at", "借出时间", 150),
                ("due_date", "到期日", 100),
                ("returned_at", "归还/处理时间", 150),
                ("status", "状态", 80),
            ],
            height=6,
        )

    def new_reader(self) -> None:
        self.reader_selected_id = None
        for key, var in self.reader_vars.items():
            var.set("正常" if key == "status" else "")
        clear_tree(self.reader_loans_tree)
        clear_tree(self.reader_history_tree)

    def save_reader(self) -> None:
        def action() -> None:
            self.reader_selected_id = self.service.save_reader(
                self.reader_vars["name"].get(),
                self.reader_vars["phone"].get(),
                self.reader_vars["department"].get(),
                self.reader_vars["contact"].get(),
                self.reader_vars["status"].get(),
                self.reader_selected_id,
            )

        self.run_safe(action, "读者信息已保存")

    def refresh_readers(self) -> None:
        if not hasattr(self, "readers_tree"):
            return
        clear_tree(self.readers_tree)
        for row in self.service.search_readers(self.reader_search_var.get()):
            self.readers_tree.insert(
                "",
                "end",
                values=(row["id"], row["name"], row["phone"], row["department"], row["contact"], row["status"]),
            )

    def load_reader_selected(self) -> None:
        values = tree_selection_values(self.readers_tree)
        if not values:
            return
        self.reader_selected_id = int(values[0])
        for key, value in zip(["name", "phone", "department", "contact", "status"], values[1:]):
            self.reader_vars[key].set(value)
        self.refresh_reader_loans()

    def refresh_reader_loans(self) -> None:
        if not hasattr(self, "reader_loans_tree"):
            return
        clear_tree(self.reader_loans_tree)
        clear_tree(self.reader_history_tree)
        if not self.reader_selected_id:
            return
        for row in self.service.reader_current_loans(self.reader_selected_id):
            self.reader_loans_tree.insert(
                "",
                "end",
                values=(row["loan_item_id"], row["barcode"], row["title"], row["shelf_name"], row["borrowed_at"], row["due_date"]),
            )
        status_labels = {"borrowed": "在借", "returned": "已还", "lost": "丢失"}
        for row in self.service.reader_history(self.reader_selected_id):
            handled_at = row["returned_at"] or row["lost_at"] or ""
            self.reader_history_tree.insert(
                "",
                "end",
                values=(
                    row["loan_item_id"],
                    row["barcode"],
                    row["title"],
                    row["shelf_name"],
                    row["borrowed_at"],
                    row["due_date"],
                    handled_at,
                    status_labels.get(row["status"], row["status"]),
                ),
            )

    def build_reader_lookup_panel(self, parent: tk.Misc, mode: str) -> tuple[tk.StringVar, ttk.Treeview, ttk.Label]:
        search = ttk.Frame(parent)
        search.pack(fill="x")
        var = tk.StringVar()
        ttk.Label(search, text="读者姓名/手机号").pack(side="left")
        ttk.Entry(search, textvariable=var, width=30).pack(side="left", padx=6)
        button_text = "查询读者"
        command = self.search_borrow_readers if mode == "borrow" else self.search_return_readers
        ttk.Button(search, text=button_text, command=command).pack(side="left")
        label = ttk.Label(parent, text="未选择读者", style="Hint.TLabel")
        label.pack(anchor="w", pady=6)
        tree = self.make_tree(
            parent,
            [
                ("id", "id", 0),
                ("name", "姓名", 120),
                ("phone", "手机号", 130),
                ("department", "部门", 120),
                ("status", "状态", 80),
            ],
            height=5,
        )
        return var, tree, label

    def build_borrow_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(tab, text="读者借书")
        self.borrow_search_var, self.borrow_readers_tree, self.borrow_reader_label = self.build_reader_lookup_panel(tab, "borrow")
        self.borrow_readers_tree.bind("<<TreeviewSelect>>", lambda event: self.select_borrow_reader())
        ttk.Label(tab, text="当前在借").pack(anchor="w", pady=(8, 2))
        self.borrow_current_tree = self.make_tree(
            tab,
            [
                ("loan_item_id", "item_id", 0),
                ("barcode", "条码", 130),
                ("title", "书名", 220),
                ("shelf", "书架", 100),
                ("due_date", "到期日", 100),
            ],
            height=5,
        )
        stage = ttk.LabelFrame(tab, text="本次借出流水", padding=10)
        stage.pack(fill="both", expand=True, pady=10)
        row = ttk.Frame(stage)
        row.pack(fill="x")
        self.borrow_barcode_var = tk.StringVar()
        ttk.Label(row, text="图书条码").pack(side="left")
        borrow_entry = ttk.Entry(row, textvariable=self.borrow_barcode_var, width=30)
        borrow_entry.pack(side="left", padx=6)
        borrow_entry.bind("<Return>", lambda event: self.add_borrow_barcode())
        ttk.Button(row, text="加入", command=self.add_borrow_barcode).pack(side="left")
        ttk.Button(row, text="移除选中", command=self.remove_borrow_barcode).pack(side="left", padx=6)
        ttk.Button(row, text="确认借书", command=self.confirm_borrow).pack(side="right")
        self.borrow_stage_tree = self.make_tree(
            stage,
            [("index", "序号", 60), ("barcode", "条码", 160), ("title", "书名", 260), ("status", "状态", 160)],
            height=8,
        )

    def search_borrow_readers(self) -> None:
        clear_tree(self.borrow_readers_tree)
        for row in self.service.search_readers(self.borrow_search_var.get()):
            self.borrow_readers_tree.insert("", "end", values=(row["id"], row["name"], row["phone"], row["department"], row["status"]))

    def select_borrow_reader(self) -> None:
        values = tree_selection_values(self.borrow_readers_tree)
        if not values:
            return
        self.borrow_reader_id = int(values[0])
        self.borrow_reader_label.configure(text=f"已选择：{values[1]} / {values[2]} / {values[3]}")
        self.refresh_borrow_reader()

    def refresh_borrow_reader(self) -> None:
        if not hasattr(self, "borrow_current_tree"):
            return
        clear_tree(self.borrow_current_tree)
        if not self.borrow_reader_id:
            return
        for row in self.service.reader_current_loans(self.borrow_reader_id):
            self.borrow_current_tree.insert("", "end", values=(row["loan_item_id"], row["barcode"], row["title"], row["shelf_name"], row["due_date"]))
        self.refresh_borrow_stage()

    def add_borrow_barcode(self) -> None:
        barcode = clean_text(self.borrow_barcode_var.get())
        if barcode:
            self.borrow_barcodes.append(barcode)
            self.borrow_barcode_var.set("")
            self.refresh_borrow_stage()

    def remove_borrow_barcode(self) -> None:
        values = tree_selection_values(self.borrow_stage_tree)
        if not values:
            return
        index = int(values[0]) - 1
        if 0 <= index < len(self.borrow_barcodes):
            self.borrow_barcodes.pop(index)
        self.refresh_borrow_stage()

    def refresh_borrow_stage(self) -> None:
        if not hasattr(self, "borrow_stage_tree"):
            return
        clear_tree(self.borrow_stage_tree)
        for index, barcode in enumerate(self.borrow_barcodes, start=1):
            book = self.service.find_book_by_barcode(barcode)
            self.borrow_stage_tree.insert(
                "",
                "end",
                values=(index, barcode, book["title"] if book else "", "可提交" if book else "未入库"),
            )

    def confirm_borrow(self) -> None:
        if not self.borrow_reader_id:
            messagebox.showwarning("提示", "请先选择读者", parent=self)
            return

        def action() -> None:
            self.service.borrow_books(self.borrow_reader_id or 0, self.borrow_barcodes)
            self.borrow_barcodes.clear()

        self.run_safe(action, "借书已完成")

    def build_return_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(tab, text="读者还书")
        self.return_search_var, self.return_readers_tree, self.return_reader_label = self.build_reader_lookup_panel(tab, "return")
        self.return_readers_tree.bind("<<TreeviewSelect>>", lambda event: self.select_return_reader())
        ttk.Label(tab, text="当前在借").pack(anchor="w", pady=(8, 2))
        self.return_current_tree = self.make_tree(
            tab,
            [
                ("loan_item_id", "item_id", 0),
                ("barcode", "条码", 130),
                ("title", "书名", 220),
                ("shelf", "书架", 100),
                ("due_date", "到期日", 100),
            ],
            height=7,
        )
        stage = ttk.LabelFrame(tab, text="本次归还流水", padding=10)
        stage.pack(fill="both", expand=True, pady=10)
        row = ttk.Frame(stage)
        row.pack(fill="x")
        self.return_barcode_var = tk.StringVar()
        ttk.Label(row, text="图书条码").pack(side="left")
        return_entry = ttk.Entry(row, textvariable=self.return_barcode_var, width=30)
        return_entry.pack(side="left", padx=6)
        return_entry.bind("<Return>", lambda event: self.add_return_barcode())
        ttk.Button(row, text="加入", command=self.add_return_barcode).pack(side="left")
        ttk.Button(row, text="移除选中", command=self.remove_return_barcode).pack(side="left", padx=6)
        ttk.Button(row, text="确认还书", command=self.confirm_return).pack(side="right")
        self.return_stage_tree = self.make_tree(
            stage,
            [("index", "序号", 60), ("barcode", "条码", 160), ("title", "书名", 260), ("status", "状态", 160)],
            height=8,
        )

    def search_return_readers(self) -> None:
        clear_tree(self.return_readers_tree)
        for row in self.service.search_readers(self.return_search_var.get()):
            self.return_readers_tree.insert("", "end", values=(row["id"], row["name"], row["phone"], row["department"], row["status"]))

    def select_return_reader(self) -> None:
        values = tree_selection_values(self.return_readers_tree)
        if not values:
            return
        self.return_reader_id = int(values[0])
        self.return_reader_label.configure(text=f"已选择：{values[1]} / {values[2]} / {values[3]}")
        self.refresh_return_reader()

    def refresh_return_reader(self) -> None:
        if not hasattr(self, "return_current_tree"):
            return
        clear_tree(self.return_current_tree)
        if not self.return_reader_id:
            return
        for row in self.service.reader_current_loans(self.return_reader_id):
            self.return_current_tree.insert("", "end", values=(row["loan_item_id"], row["barcode"], row["title"], row["shelf_name"], row["due_date"]))
        self.refresh_return_stage()

    def add_return_barcode(self) -> None:
        barcode = clean_text(self.return_barcode_var.get())
        if barcode:
            self.return_barcodes.append(barcode)
            self.return_barcode_var.set("")
            self.refresh_return_stage()

    def remove_return_barcode(self) -> None:
        values = tree_selection_values(self.return_stage_tree)
        if not values:
            return
        index = int(values[0]) - 1
        if 0 <= index < len(self.return_barcodes):
            self.return_barcodes.pop(index)
        self.refresh_return_stage()

    def refresh_return_stage(self) -> None:
        if not hasattr(self, "return_stage_tree"):
            return
        clear_tree(self.return_stage_tree)
        current = {row["barcode"]: row["title"] for row in self.service.reader_current_loans(self.return_reader_id or 0)} if self.return_reader_id else {}
        for index, barcode in enumerate(self.return_barcodes, start=1):
            self.return_stage_tree.insert(
                "",
                "end",
                values=(index, barcode, current.get(barcode, ""), "可提交" if barcode in current else "当前读者未借"),
            )

    def confirm_return(self) -> None:
        if not self.return_reader_id:
            messagebox.showwarning("提示", "请先选择读者", parent=self)
            return

        def action() -> None:
            self.service.return_books(self.return_reader_id or 0, self.return_barcodes)
            self.return_barcodes.clear()

        self.run_safe(action, "还书已完成")

    def build_settings_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(tab, text="系统设置")
        days = ttk.LabelFrame(tab, text="借阅规则", padding=10)
        days.pack(fill="x")
        self.default_days_var = tk.StringVar()
        self.renew_days_var = tk.StringVar()
        ttk.Label(days, text="默认借阅天数").pack(side="left")
        ttk.Entry(days, textvariable=self.default_days_var, width=8).pack(side="left", padx=6)
        ttk.Label(days, text="默认续借天数").pack(side="left", padx=(16, 0))
        ttk.Entry(days, textvariable=self.renew_days_var, width=8).pack(side="left", padx=6)
        ttk.Button(days, text="保存规则", command=self.save_settings_days).pack(side="left", padx=8)

        shelves_frame = ttk.LabelFrame(tab, text="书架配置", padding=10)
        shelves_frame.pack(fill="both", expand=True, pady=10)
        row = ttk.Frame(shelves_frame)
        row.pack(fill="x")
        self.new_shelf_name_var = tk.StringVar()
        self.new_shelf_note_var = tk.StringVar()
        ttk.Label(row, text="书架名").pack(side="left")
        ttk.Entry(row, textvariable=self.new_shelf_name_var, width=20).pack(side="left", padx=6)
        ttk.Label(row, text="备注").pack(side="left")
        ttk.Entry(row, textvariable=self.new_shelf_note_var, width=30).pack(side="left", padx=6)
        ttk.Button(row, text="新增书架", command=self.add_shelf).pack(side="left")
        ttk.Button(row, text="停用/启用选中", command=self.toggle_selected_shelf).pack(side="left", padx=6)
        self.settings_shelves_tree = self.make_tree(
            shelves_frame,
            [("id", "id", 0), ("name", "书架名称", 180), ("active", "状态", 80), ("note", "备注", 260)],
            height=6,
        )

        reasons_frame = ttk.LabelFrame(tab, text="原因配置", padding=10)
        reasons_frame.pack(fill="both", expand=True)
        rrow = ttk.Frame(reasons_frame)
        rrow.pack(fill="x")
        self.reason_category_var = tk.StringVar(value=REASON_LABELS["stock_adjust"])
        self.new_reason_var = tk.StringVar()
        ttk.Label(rrow, text="分类").pack(side="left")
        self.reason_category_combo = ttk.Combobox(
            rrow,
            textvariable=self.reason_category_var,
            values=list(REASON_BY_LABEL.keys()),
            width=16,
            state="readonly",
        )
        self.reason_category_combo.pack(side="left", padx=6)
        self.reason_category_combo.bind("<<ComboboxSelected>>", lambda event: self.refresh_settings_reasons())
        ttk.Label(rrow, text="原因").pack(side="left")
        ttk.Entry(rrow, textvariable=self.new_reason_var, width=24).pack(side="left", padx=6)
        ttk.Button(rrow, text="新增原因", command=self.add_reason).pack(side="left")
        ttk.Button(rrow, text="停用/启用选中", command=self.toggle_selected_reason).pack(side="left", padx=6)
        self.settings_reasons_tree = self.make_tree(
            reasons_frame,
            [("id", "id", 0), ("name", "原因", 220), ("active", "状态", 80)],
            height=7,
        )

    def save_settings_days(self) -> None:
        def action() -> None:
            default_days = int(self.default_days_var.get())
            renewal_days = int(self.renew_days_var.get())
            if default_days <= 0 or renewal_days <= 0:
                raise LibraryError("天数必须大于 0")
            self.service.set_setting("default_borrow_days", str(default_days))
            self.service.set_setting("renewal_days", str(renewal_days))

        self.run_safe(action, "借阅规则已保存")

    def add_shelf(self) -> None:
        def action() -> None:
            self.service.add_shelf(self.new_shelf_name_var.get(), self.new_shelf_note_var.get())
            self.new_shelf_name_var.set("")
            self.new_shelf_note_var.set("")

        self.run_safe(action, "书架已新增")

    def toggle_selected_shelf(self) -> None:
        values = tree_selection_values(self.settings_shelves_tree)
        if not values:
            messagebox.showwarning("提示", "请选择书架", parent=self)
            return

        def action() -> None:
            self.service.set_shelf_active(int(values[0]), values[2] == "停用")

        self.run_safe(action, "书架状态已更新")

    def add_reason(self) -> None:
        def action() -> None:
            category = REASON_BY_LABEL[self.reason_category_var.get()]
            self.service.add_reason(category, self.new_reason_var.get())
            self.new_reason_var.set("")

        self.run_safe(action, "原因已新增")

    def toggle_selected_reason(self) -> None:
        values = tree_selection_values(self.settings_reasons_tree)
        if not values:
            messagebox.showwarning("提示", "请选择原因", parent=self)
            return

        def action() -> None:
            self.service.set_reason_active(int(values[0]), values[2] == "停用")

        self.run_safe(action, "原因状态已更新")

    def refresh_settings(self) -> None:
        if not hasattr(self, "settings_shelves_tree"):
            return
        self.default_days_var.set(self.service.get_setting("default_borrow_days", "30"))
        self.renew_days_var.set(self.service.get_setting("renewal_days", "15"))
        clear_tree(self.settings_shelves_tree)
        for row in self.service.list_shelves(active_only=False):
            self.settings_shelves_tree.insert(
                "",
                "end",
                values=(row["id"], row["name"], "启用" if row["active"] else "停用", row["note"]),
            )
        self.refresh_settings_reasons()

    def refresh_settings_reasons(self) -> None:
        if not hasattr(self, "settings_reasons_tree"):
            return
        clear_tree(self.settings_reasons_tree)
        category = REASON_BY_LABEL.get(self.reason_category_var.get(), "stock_adjust")
        for row in self.service.list_reasons(category, active_only=False):
            self.settings_reasons_tree.insert(
                "",
                "end",
                values=(row["id"], row["name"], "启用" if row["active"] else "停用"),
            )


def main() -> None:
    startup_log("准备启动应用")
    app = LibraryApp()
    startup_log("进入窗口主循环")
    app.mainloop()
    startup_log("窗口已关闭")
