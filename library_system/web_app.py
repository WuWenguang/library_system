from __future__ import annotations

import json
import socket
import threading
import time
import urllib.parse
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Optional, Union

from .db import DEFAULT_DB_PATH, Database
from .services import LibraryError, LibraryService


APP_VERSION = "0.3.0"


def log(message: str) -> None:
    print(f"[图书管理系统] {message}", flush=True)


def find_port(start: int = 8765) -> int:
    for port in range(start, start + 80):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("未找到可用端口")


def parse_int(value: Any) -> Optional[int]:
    if value in (None, "", "all"):
        return None
    return int(value)


class WebLibraryApp:
    def __init__(self, db_path: Union[str, Path] = DEFAULT_DB_PATH) -> None:
        self.service = LibraryService(Database(db_path))

    def make_handler(self) -> type[BaseHTTPRequestHandler]:
        app = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args: Any) -> None:
                return

            def do_GET(self) -> None:
                app.handle(self, "GET")

            def do_POST(self) -> None:
                app.handle(self, "POST")

            def do_PUT(self) -> None:
                app.handle(self, "PUT")

            def do_DELETE(self) -> None:
                app.handle(self, "DELETE")

        return Handler

    def handle(self, handler: BaseHTTPRequestHandler, method: str) -> None:
        parsed = urllib.parse.urlparse(handler.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        try:
            if method == "GET" and path == "/":
                self.send_html(handler, INDEX_HTML)
                return
            if method == "GET" and path == "/api/initial":
                self.send_json(handler, self.initial_data())
                return
            if method == "GET" and path == "/api/dashboard":
                self.send_json(
                    handler,
                    {
                        "stats": self.service.dashboard_stats(),
                        "overdue": self.service.list_overdue(),
                    },
                )
                return
            if method == "GET" and path == "/api/book":
                barcode = query.get("barcode", [""])[0]
                self.send_json(handler, {"book": self.service.find_book_by_barcode(barcode)})
                return
            if method == "GET" and path == "/api/inbound/items":
                batch_id = int(query.get("batch_id", ["0"])[0])
                self.send_json(handler, {"items": self.service.list_inbound_items(batch_id)})
                return
            if method == "GET" and path == "/api/inventory":
                keyword = query.get("keyword", [""])[0]
                shelf_id = parse_int(query.get("shelf_id", [""])[0])
                self.send_json(handler, {"items": self.service.list_inventory(keyword, shelf_id)})
                return
            if method == "GET" and path == "/api/readers":
                keyword = query.get("query", [""])[0]
                self.send_json(handler, {"readers": self.service.search_readers(keyword)})
                return
            if method == "GET" and path == "/api/reader_loans":
                reader_id = int(query.get("reader_id", ["0"])[0])
                self.send_json(
                    handler,
                    {
                        "current": self.service.reader_current_loans(reader_id),
                        "history": self.service.reader_history(reader_id),
                    },
                )
                return
            if method == "GET" and path == "/export/inventory.csv":
                shelf_id = parse_int(query.get("shelf_id", [""])[0])
                self.send_inventory_csv(handler, shelf_id)
                return

            data = self.read_json(handler)
            if method == "POST" and path == "/api/inbound/batch":
                batch_id = self.service.create_inbound_batch(data.get("note", ""))
                self.send_json(handler, {"batch": self.service.get_batch(batch_id)})
                return
            if method == "POST" and path == "/api/inbound/items":
                item_id = self.service.add_inbound_item(
                    int(data["batch_id"]),
                    data.get("book", {}),
                    int(data["shelf_id"]),
                    data["quantity"],
                    data.get("note", ""),
                )
                self.send_json(handler, {"id": item_id})
                return
            if method == "PUT" and path.startswith("/api/inbound/items/"):
                item_id = int(path.rsplit("/", 1)[1])
                self.service.update_inbound_item(
                    item_id,
                    data.get("book", {}),
                    int(data["shelf_id"]),
                    data["quantity"],
                    data.get("note", ""),
                )
                self.send_json(handler, {"ok": True})
                return
            if method == "DELETE" and path.startswith("/api/inbound/items/"):
                item_id = int(path.rsplit("/", 1)[1])
                self.service.delete_inbound_item(item_id)
                self.send_json(handler, {"ok": True})
                return
            if method == "POST" and path == "/api/inbound/confirm":
                self.service.confirm_inbound_batch(int(data["batch_id"]))
                self.send_json(handler, {"ok": True})
                return
            if method == "POST" and path == "/api/inventory/adjust":
                self.service.adjust_book_inventory(
                    int(data["book_id"]),
                    int(data["shelf_id"]),
                    data["quantity"],
                    data.get("book", {}),
                    int(data["reason_id"]),
                    data.get("note", ""),
                )
                self.send_json(handler, {"ok": True})
                return
            if method == "POST" and path == "/api/readers":
                reader_id = self.service.save_reader(
                    data.get("name", ""),
                    data.get("phone", ""),
                    data.get("department", ""),
                    data.get("contact", ""),
                    data.get("status", "正常"),
                    parse_int(data.get("id")),
                )
                self.send_json(handler, {"id": reader_id})
                return
            if method == "POST" and path == "/api/borrow":
                loan_id = self.service.borrow_books(int(data["reader_id"]), data.get("barcodes", []))
                self.send_json(handler, {"loan_id": loan_id})
                return
            if method == "POST" and path == "/api/return":
                self.service.return_books(int(data["reader_id"]), data.get("barcodes", []))
                self.send_json(handler, {"ok": True})
                return
            if method == "POST" and path == "/api/renew":
                self.service.renew_loan(
                    int(data["loan_id"]),
                    data["days"],
                    int(data["reason_id"]),
                    data.get("note", ""),
                )
                self.send_json(handler, {"ok": True})
                return
            if method == "POST" and path == "/api/lost":
                self.service.mark_lost(
                    int(data["loan_item_id"]),
                    int(data["reason_id"]),
                    data.get("note", ""),
                )
                self.send_json(handler, {"ok": True})
                return
            if method == "POST" and path == "/api/settings":
                self.service.set_setting("default_borrow_days", data.get("default_borrow_days", "30"))
                self.service.set_setting("renewal_days", data.get("renewal_days", "15"))
                self.send_json(handler, {"ok": True})
                return
            if method == "POST" and path == "/api/shelves":
                shelf_id = self.service.add_shelf(data.get("name", ""), data.get("note", ""))
                self.send_json(handler, {"id": shelf_id})
                return
            if method == "PUT" and path.startswith("/api/shelves/"):
                shelf_id = int(path.rsplit("/", 1)[1])
                self.service.update_shelf(shelf_id, data.get("name", ""), data.get("note", ""))
                self.send_json(handler, {"ok": True})
                return
            if method == "DELETE" and path.startswith("/api/shelves/"):
                shelf_id = int(path.rsplit("/", 1)[1])
                self.service.set_shelf_active(shelf_id, False)
                self.send_json(handler, {"ok": True})
                return
            if method == "POST" and path == "/api/reasons":
                reason_id = self.service.add_reason(data.get("category", ""), data.get("name", ""))
                self.send_json(handler, {"id": reason_id})
                return
            if method == "PUT" and path.startswith("/api/reasons/"):
                reason_id = int(path.rsplit("/", 1)[1])
                self.service.update_reason(reason_id, data.get("category", ""), data.get("name", ""))
                self.send_json(handler, {"ok": True})
                return
            if method == "DELETE" and path.startswith("/api/reasons/"):
                reason_id = int(path.rsplit("/", 1)[1])
                self.service.set_reason_active(reason_id, False)
                self.send_json(handler, {"ok": True})
                return

            self.send_json(handler, {"error": "未找到接口"}, HTTPStatus.NOT_FOUND)
        except LibraryError as exc:
            self.send_json(handler, {"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self.send_json(handler, {"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def initial_data(self) -> dict[str, Any]:
        return {
            "version": APP_VERSION,
            "shelves": self.service.list_shelves(active_only=True),
            "reasons": {
                "stock_adjust": self.service.list_reasons("stock_adjust"),
                "renewal": self.service.list_reasons("renewal"),
                "lost_damage": self.service.list_reasons("lost_damage"),
            },
            "settings": {
                "default_borrow_days": self.service.get_setting("default_borrow_days", "30"),
                "renewal_days": self.service.get_setting("renewal_days", "15"),
            },
        }

    def read_json(self, handler: BaseHTTPRequestHandler) -> dict[str, Any]:
        length = int(handler.headers.get("Content-Length", "0") or "0")
        if length == 0:
            return {}
        raw = handler.rfile.read(length).decode("utf-8")
        return json.loads(raw or "{}")

    def send_json(
        self,
        handler: BaseHTTPRequestHandler,
        payload: dict[str, Any],
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    def send_html(self, handler: BaseHTTPRequestHandler, html: str) -> None:
        body = html.encode("utf-8")
        handler.send_response(HTTPStatus.OK)
        handler.send_header("Content-Type", "text/html; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    def send_inventory_csv(self, handler: BaseHTTPRequestHandler, shelf_id: Optional[int]) -> None:
        from tempfile import NamedTemporaryFile

        with NamedTemporaryFile("w+", suffix=".csv", delete=False, encoding="utf-8-sig", newline="") as tmp:
            path = tmp.name
        try:
            self.service.export_inventory_csv(path, shelf_id)
            body = Path(path).read_bytes()
        finally:
            Path(path).unlink(missing_ok=True)
        handler.send_response(HTTPStatus.OK)
        handler.send_header("Content-Type", "text/csv; charset=utf-8")
        handler.send_header("Content-Disposition", 'attachment; filename="inventory.csv"')
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)


INDEX_HTML = r"""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>图书借阅管理系统</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f5f7fb;
      --panel: #ffffff;
      --line: #d9e1ec;
      --text: #152033;
      --muted: #607086;
      --brand: #2563eb;
      --brand-dark: #1d4ed8;
      --danger: #dc2626;
      --ok: #047857;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
      font-size: 14px;
    }
    header {
      height: 56px;
      background: #172033;
      color: white;
      display: flex;
      align-items: center;
      padding: 0 22px;
      gap: 14px;
      box-shadow: 0 1px 8px rgba(15, 23, 42, 0.18);
    }
    header h1 { font-size: 19px; margin: 0; }
    header span { color: #cbd5e1; }
    main { display: grid; grid-template-columns: 190px 1fr; min-height: calc(100vh - 56px); }
    nav {
      background: #edf2f7;
      border-right: 1px solid var(--line);
      padding: 14px 10px;
    }
    nav button {
      width: 100%;
      display: block;
      text-align: left;
      border: 0;
      background: transparent;
      color: var(--text);
      padding: 11px 12px;
      margin: 2px 0;
      border-radius: 6px;
      cursor: pointer;
      font-size: 14px;
    }
    nav button.active { background: var(--brand); color: white; }
    .content { padding: 18px; overflow: auto; }
    .page { display: none; }
    .page.active { display: block; }
    .toolbar, .form-grid, .cards {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      margin-bottom: 12px;
    }
    .toolbar { display: flex; gap: 10px; flex-wrap: wrap; align-items: end; }
    .form-grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(180px, 1fr));
      gap: 10px;
      align-items: end;
    }
    .cards { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
    .card {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      background: #fbfdff;
    }
    .card strong { display: block; font-size: 24px; margin-top: 6px; }
    label { display: block; color: var(--muted); font-size: 12px; margin-bottom: 4px; }
    input, select, textarea {
      width: 100%;
      height: 34px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: white;
      color: var(--text);
      padding: 6px 8px;
      font-size: 14px;
    }
    textarea { height: 70px; resize: vertical; }
    button.primary, button.secondary, button.danger {
      height: 34px;
      border: 0;
      border-radius: 6px;
      padding: 0 12px;
      color: white;
      cursor: pointer;
      white-space: nowrap;
    }
    button.primary { background: var(--brand); }
    button.primary:hover { background: var(--brand-dark); }
    button.secondary { background: #475569; }
    button.danger { background: var(--danger); }
    table {
      width: 100%;
      border-collapse: collapse;
      background: white;
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
      margin-bottom: 14px;
    }
    th, td {
      border-bottom: 1px solid var(--line);
      padding: 8px 9px;
      text-align: left;
      vertical-align: top;
      font-size: 13px;
    }
    th { background: #edf2f7; color: #344256; position: sticky; top: 0; }
    tr:hover td { background: #f8fafc; }
    tr.selected td { background: #dbeafe; }
    h2 { margin: 0 0 12px; font-size: 20px; }
    h3 { margin: 18px 0 8px; font-size: 15px; }
    .status { min-height: 22px; margin-bottom: 10px; color: var(--muted); }
    .status.error { color: var(--danger); }
    .status.ok { color: var(--ok); }
    .two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
    @media (max-width: 900px) {
      main { grid-template-columns: 1fr; }
      nav { display: flex; overflow-x: auto; }
      nav button { width: auto; white-space: nowrap; }
      .form-grid, .cards, .two-col { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <h1>图书借阅管理系统</h1>
    <span id="version"></span>
  </header>
  <main>
    <nav id="tabs"></nav>
    <section class="content">
      <div id="status" class="status">正在加载...</div>

      <section id="dashboard" class="page active">
        <h2>首页</h2>
        <div class="cards">
          <div class="card">今日借出<strong id="statBorrowed">0</strong></div>
          <div class="card">今日归还<strong id="statReturned">0</strong></div>
          <div class="card">逾期未还<strong id="statOverdue">0</strong></div>
          <div class="card">零库存条目<strong id="statZero">0</strong></div>
        </div>
        <h3>逾期未还读者提醒</h3>
        <table id="overdueTable"></table>
      </section>

      <section id="inbound" class="page">
        <h2>图书入库</h2>
        <div class="toolbar">
          <div><label>批次备注</label><input id="batchNote"></div>
          <button class="primary" onclick="createBatch()">新建批次</button>
          <button class="secondary" onclick="confirmBatch()">确认入库</button>
          <span id="batchText">当前批次：未创建</span>
        </div>
        <div class="form-grid">
          <div><label>条码</label><input id="inBarcode" onkeydown="if(event.key==='Enter') lookupBook()"></div>
          <div><label>书名</label><input id="inTitle"></div>
          <div><label>作者</label><input id="inAuthor"></div>
          <div><label>出版社</label><input id="inPublisher"></div>
          <div><label>书架</label><select id="inShelf"></select></div>
          <div><label>数量</label><input id="inQty" value="1"></div>
          <div><label>备注</label><input id="inNote"></div>
          <button class="secondary" onclick="lookupBook()">查询条码</button>
          <button class="primary" onclick="saveInboundItem(false)">添加流水</button>
          <button class="secondary" onclick="saveInboundItem(true)">修改选中</button>
          <button class="danger" onclick="deleteInboundItem()">删除选中</button>
        </div>
        <table id="inboundTable"></table>
      </section>

      <section id="inventory" class="page">
        <h2>图书库存/调整</h2>
        <div class="toolbar">
          <div><label>关键词</label><input id="invKeyword"></div>
          <div><label>书架</label><select id="invShelfFilter"></select></div>
          <button class="primary" onclick="loadInventory()">查询</button>
          <button class="secondary" onclick="exportInventory()">导出 CSV</button>
        </div>
        <table id="inventoryTable"></table>
        <div class="form-grid">
          <div><label>书名</label><input id="editTitle"></div>
          <div><label>作者</label><input id="editAuthor"></div>
          <div><label>出版社</label><input id="editPublisher"></div>
          <div><label>价格</label><input id="editPrice"></div>
          <div><label>出版日期</label><input id="editPublishDate"></div>
          <div><label>库存数量</label><input id="editQty"></div>
          <div><label>修改原因</label><select id="editReason"></select></div>
          <div><label>备注</label><input id="editNote"></div>
          <button class="primary" onclick="adjustInventory()">保存修改</button>
        </div>
      </section>

      <section id="readers" class="page">
        <h2>读者管理</h2>
        <div class="form-grid">
          <input type="hidden" id="readerId">
          <div><label>姓名</label><input id="readerName"></div>
          <div><label>手机号</label><input id="readerPhone"></div>
          <div><label>部门</label><input id="readerDept"></div>
          <div><label>联系方式</label><input id="readerContact"></div>
          <div><label>状态</label><select id="readerStatus"><option>正常</option><option>停用</option></select></div>
          <button class="secondary" onclick="newReader()">新增</button>
          <button class="primary" onclick="saveReader()">保存</button>
        </div>
        <div class="toolbar">
          <div><label>查询</label><input id="readerQuery"></div>
          <button class="primary" onclick="loadReaders()">搜索</button>
        </div>
        <div class="two-col">
          <div><h3>读者列表</h3><table id="readerTable"></table></div>
          <div><h3>当前在借</h3><table id="readerLoanTable"></table><h3>历史借阅</h3><table id="readerHistoryTable"></table></div>
        </div>
      </section>

      <section id="borrow" class="page">
        <h2>读者借书</h2>
        <div class="toolbar">
          <div><label>姓名/手机号</label><input id="borrowReaderQuery"></div>
          <button class="primary" onclick="searchBorrowReaders()">查询读者</button>
          <span id="borrowReaderText">未选择读者</span>
        </div>
        <table id="borrowReaderTable"></table>
        <h3>当前在借</h3><table id="borrowCurrentTable"></table>
        <div class="toolbar">
          <div><label>图书条码</label><input id="borrowBarcode" onkeydown="if(event.key==='Enter') addBorrowBarcode()"></div>
          <button class="secondary" onclick="addBorrowBarcode()">加入</button>
          <button class="primary" onclick="confirmBorrow()">确认借书</button>
        </div>
        <table id="borrowStageTable"></table>
      </section>

      <section id="returns" class="page">
        <h2>读者还书</h2>
        <div class="toolbar">
          <div><label>姓名/手机号</label><input id="returnReaderQuery"></div>
          <button class="primary" onclick="searchReturnReaders()">查询读者</button>
          <span id="returnReaderText">未选择读者</span>
        </div>
        <table id="returnReaderTable"></table>
        <h3>当前在借</h3><table id="returnCurrentTable"></table>
        <div class="toolbar">
          <div><label>归还条码</label><input id="returnBarcode" onkeydown="if(event.key==='Enter') addReturnBarcode()"></div>
          <button class="secondary" onclick="addReturnBarcode()">加入</button>
          <button class="primary" onclick="confirmReturn()">确认还书</button>
        </div>
        <table id="returnStageTable"></table>
      </section>

      <section id="settings" class="page">
        <h2>系统设置</h2>
        <div class="form-grid">
          <div><label>默认借阅天数</label><input id="defaultBorrowDays"></div>
          <div><label>默认续借天数</label><input id="renewalDays"></div>
          <button class="primary" onclick="saveSettings()">保存规则</button>
        </div>
        <h3>书架配置</h3>
        <div class="toolbar">
          <div><label>书架名</label><input id="newShelf"></div>
          <div><label>备注</label><input id="newShelfNote"></div>
          <button class="primary" onclick="addShelf()">新增书架</button>
          <button class="secondary" onclick="updateShelf()">保存修改</button>
          <button class="danger" onclick="deleteShelf()">删除选中</button>
        </div>
        <table id="shelfTable"></table>
        <h3>原因配置</h3>
        <div class="toolbar">
          <div><label>分类</label><select id="reasonCategory"><option value="stock_adjust">库存/图书修改</option><option value="renewal">续借原因</option><option value="lost_damage">丢失报损原因</option></select></div>
          <div><label>原因</label><input id="newReason"></div>
          <button class="primary" onclick="addReason()">新增原因</button>
          <button class="secondary" onclick="updateReason()">保存修改</button>
          <button class="danger" onclick="deleteReason()">删除选中</button>
        </div>
        <table id="reasonTable"></table>
      </section>
    </section>
  </main>
<script>
const pages = [
  ["dashboard", "首页"], ["inbound", "图书入库"], ["inventory", "库存调整"],
  ["readers", "读者管理"], ["borrow", "读者借书"], ["returns", "读者还书"], ["settings", "系统设置"]
];
let state = { shelves: [], reasons: {}, settings: {}, currentBatch: null, selectedInbound: null,
  selectedInventory: null, selectedReader: null, borrowReader: null, returnReader: null,
  selectedShelf: null, selectedReason: null, borrowBarcodes: [], returnBarcodes: [] };

function $(id) { return document.getElementById(id); }
function setStatus(text, cls="") { const el=$("status"); el.textContent=text; el.className="status " + cls; }
function value(id) { return $(id).value.trim(); }
function fill(id, rows, label, valueKey="id", includeAll=false) {
  const el = $(id); el.innerHTML = includeAll ? '<option value="">全部</option>' : '';
  rows.forEach(row => { const opt=document.createElement("option"); opt.value=row[valueKey]; opt.textContent=row[label]; el.appendChild(opt); });
}
async function api(path, options={}) {
  const res = await fetch(path, { headers: {"Content-Type": "application/json"}, ...options });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || "请求失败");
  return data;
}
async function mutate(path, body, method="POST") {
  const data = await api(path, { method, body: JSON.stringify(body) });
  setStatus("操作成功", "ok");
  return data;
}
function renderTable(id, columns, rows, onClick) {
  const table = $(id);
  table.innerHTML = "<thead><tr>" + columns.map(c => `<th>${c[1]}</th>`).join("") + "</tr></thead><tbody></tbody>";
  const body = table.querySelector("tbody");
  rows.forEach(row => {
    const tr = document.createElement("tr");
    tr.innerHTML = columns.map(c => `<td>${row[c[0]] ?? ""}</td>`).join("");
    tr.onclick = () => { [...body.children].forEach(x => x.classList.remove("selected")); tr.classList.add("selected"); if (onClick) onClick(row); };
    body.appendChild(tr);
  });
}
function setupTabs() {
  const tabs = $("tabs");
  pages.forEach(([id, name], index) => {
    const btn = document.createElement("button");
    btn.textContent = name;
    btn.className = index === 0 ? "active" : "";
    btn.onclick = () => {
      document.querySelectorAll("nav button").forEach(b => b.classList.remove("active"));
      document.querySelectorAll(".page").forEach(p => p.classList.remove("active"));
      btn.classList.add("active"); $(id).classList.add("active");
      if (id === "dashboard") loadDashboard();
      if (id === "inventory") loadInventory();
      if (id === "readers") loadReaders();
    };
    tabs.appendChild(btn);
  });
}
async function loadInitial() {
  state = { ...state, ...(await api("/api/initial")) };
  $("version").textContent = "v" + state.version + " 本地版";
  fill("inShelf", state.shelves, "name");
  fill("invShelfFilter", state.shelves, "name", "id", true);
  fill("editReason", state.reasons.stock_adjust, "name");
  $("defaultBorrowDays").value = state.settings.default_borrow_days;
  $("renewalDays").value = state.settings.renewal_days;
  renderSettingsTables();
  setStatus("已连接本地数据库", "ok");
}
async function refreshLookups() { await loadInitial(); }
async function loadDashboard() {
  const data = await api("/api/dashboard");
  $("statBorrowed").textContent = data.stats.borrowed_today;
  $("statReturned").textContent = data.stats.returned_today;
  $("statOverdue").textContent = data.stats.overdue;
  $("statZero").textContent = data.stats.zero_stock;
  renderTable("overdueTable", [["reader_name","读者"],["phone","手机号"],["department","部门"],["barcode","条码"],["title","书名"],["shelf_name","书架"],["due_date","到期日"],["days_overdue","逾期天数"],["actions","操作"]],
    data.overdue.map(r => ({...r, actions:"续借 / 报损"})), row => overdueAction(row));
}
async function overdueAction(row) {
  const action = prompt("输入 1 续借，输入 2 丢失报损", "1");
  if (action === "1") {
    const days = prompt("续借天数", state.settings.renewal_days || "15");
    const reason = state.reasons.renewal[0];
    await mutate("/api/renew", {loan_id: row.loan_id, days, reason_id: reason.id, note: prompt("备注", "") || ""});
  } else if (action === "2") {
    const reason = state.reasons.lost_damage[0];
    await mutate("/api/lost", {loan_item_id: row.loan_item_id, reason_id: reason.id, note: prompt("报损备注", "") || ""});
  }
  await loadDashboard();
}
async function createBatch() {
  const data = await mutate("/api/inbound/batch", {note: value("batchNote")});
  state.currentBatch = data.batch;
  $("batchText").textContent = "当前批次：" + data.batch.code;
  await loadInboundItems();
}
async function lookupBook() {
  const data = await api("/api/book?barcode=" + encodeURIComponent(value("inBarcode")));
  if (data.book) {
    $("inTitle").value = data.book.title; $("inAuthor").value = data.book.author; $("inPublisher").value = data.book.publisher;
    setStatus("该图书已入库，可直接录入入库数量", "ok");
  } else {
    setStatus("新书条码，请录入详细信息");
  }
}
function inboundBook() {
  return {barcode:value("inBarcode"), title:value("inTitle"), author:value("inAuthor"), publisher:value("inPublisher")};
}
async function saveInboundItem(edit) {
  if (!state.currentBatch) await createBatch();
  const body = {batch_id: state.currentBatch.id, book: inboundBook(), shelf_id: value("inShelf"), quantity:value("inQty"), note:value("inNote")};
  if (edit) {
    if (!state.selectedInbound) throw new Error("请选择流水");
    await mutate("/api/inbound/items/" + state.selectedInbound.id, body, "PUT");
  } else {
    await mutate("/api/inbound/items", body);
  }
  await loadInboundItems();
}
async function deleteInboundItem() {
  if (!state.selectedInbound) return setStatus("请选择流水", "error");
  await mutate("/api/inbound/items/" + state.selectedInbound.id, {}, "DELETE");
  state.selectedInbound = null; await loadInboundItems();
}
async function loadInboundItems() {
  if (!state.currentBatch) return renderTable("inboundTable", [["empty","流水"]], []);
  const data = await api("/api/inbound/items?batch_id=" + state.currentBatch.id);
  renderTable("inboundTable", [["barcode","条码"],["title","书名"],["author","作者"],["publisher","出版社"],["shelf_name","书架"],["quantity","数量"],["note","备注"]], data.items, row => {
    state.selectedInbound=row; $("inBarcode").value=row.barcode; $("inTitle").value=row.title; $("inAuthor").value=row.author; $("inPublisher").value=row.publisher;
    $("inShelf").value=row.shelf_id; $("inQty").value=row.quantity; $("inNote").value=row.note;
  });
}
async function confirmBatch() {
  if (!state.currentBatch) return setStatus("请先创建批次", "error");
  if (!confirm("确认将当前批次统一写入库存？")) return;
  await mutate("/api/inbound/confirm", {batch_id: state.currentBatch.id});
  state.currentBatch = null; $("batchText").textContent = "当前批次：未创建"; await loadInboundItems(); await loadDashboard();
}
async function loadInventory() {
  const q = new URLSearchParams({keyword:value("invKeyword"), shelf_id:value("invShelfFilter")});
  const data = await api("/api/inventory?" + q);
  renderTable("inventoryTable", [["barcode","条码"],["title","书名"],["author","作者"],["publisher","出版社"],["shelf_name","书架"],["quantity","库存"]], data.items, row => {
    state.selectedInventory=row; $("editTitle").value=row.title; $("editAuthor").value=row.author; $("editPublisher").value=row.publisher;
    $("editPrice").value=row.price;
    $("editPublishDate").value=row.publish_date; $("editQty").value=row.quantity; $("editNote").value="";
  });
}
async function adjustInventory() {
  if (!state.selectedInventory) return setStatus("请选择库存记录", "error");
  await mutate("/api/inventory/adjust", {book_id:state.selectedInventory.book_id, shelf_id:state.selectedInventory.shelf_id, quantity:value("editQty"),
    reason_id:value("editReason"), note:value("editNote"), book:{title:value("editTitle"), author:value("editAuthor"), publisher:value("editPublisher"), price:value("editPrice"), publish_date:value("editPublishDate"), description:""}});
  await loadInventory();
}
function exportInventory() { window.location.href = "/export/inventory.csv?shelf_id=" + encodeURIComponent(value("invShelfFilter")); }
async function loadReaders() {
  const data = await api("/api/readers?query=" + encodeURIComponent(value("readerQuery")));
  renderTable("readerTable", [["name","姓名"],["phone","手机号"],["department","部门"],["contact","联系方式"],["status","状态"]], data.readers, row => selectReader(row));
}
function newReader() { ["readerId","readerName","readerPhone","readerDept","readerContact"].forEach(id => $(id).value=""); $("readerStatus").value="正常"; }
async function saveReader() {
  await mutate("/api/readers", {id:value("readerId"), name:value("readerName"), phone:value("readerPhone"), department:value("readerDept"), contact:value("readerContact"), status:value("readerStatus")});
  await loadReaders();
}
async function selectReader(row) {
  state.selectedReader=row; $("readerId").value=row.id; $("readerName").value=row.name; $("readerPhone").value=row.phone; $("readerDept").value=row.department; $("readerContact").value=row.contact; $("readerStatus").value=row.status;
  const data = await api("/api/reader_loans?reader_id=" + row.id);
  renderLoanTables("readerLoanTable", "readerHistoryTable", data);
}
function renderLoanTables(currentId, historyId, data) {
  renderTable(currentId, [["barcode","条码"],["title","书名"],["shelf_name","书架"],["borrowed_at","借出时间"],["due_date","到期日"]], data.current);
  renderTable(historyId, [["barcode","条码"],["title","书名"],["shelf_name","书架"],["borrowed_at","借出时间"],["due_date","到期日"],["status","状态"]], data.history);
}
async function searchBorrowReaders() { await searchBorrowReturn("borrow"); }
async function searchReturnReaders() { await searchBorrowReturn("return"); }
async function searchBorrowReturn(kind) {
  const prefix = kind === "borrow" ? "borrow" : "return";
  const data = await api("/api/readers?query=" + encodeURIComponent(value(prefix + "ReaderQuery")));
  renderTable(prefix + "ReaderTable", [["name","姓名"],["phone","手机号"],["department","部门"],["status","状态"]], data.readers, async row => {
    state[prefix + "Reader"] = row; $(prefix + "ReaderText").textContent = "已选择：" + row.name + " / " + row.phone;
    const loans = await api("/api/reader_loans?reader_id=" + row.id);
    renderTable(prefix + "CurrentTable", [["barcode","条码"],["title","书名"],["shelf_name","书架"],["due_date","到期日"]], loans.current);
  });
}
function addBorrowBarcode() { const b=value("borrowBarcode"); if (b) { state.borrowBarcodes.push(b); $("borrowBarcode").value=""; renderStage("borrowStageTable", state.borrowBarcodes); } }
function addReturnBarcode() { const b=value("returnBarcode"); if (b) { state.returnBarcodes.push(b); $("returnBarcode").value=""; renderStage("returnStageTable", state.returnBarcodes); } }
function renderStage(id, rows) { renderTable(id, [["i","序号"],["barcode","条码"]], rows.map((barcode, i)=>({i:i+1, barcode}))); }
async function confirmBorrow() { if (!state.borrowReader) return setStatus("请先选择读者", "error"); await mutate("/api/borrow", {reader_id:state.borrowReader.id, barcodes:state.borrowBarcodes}); state.borrowBarcodes=[]; renderStage("borrowStageTable", []); await searchBorrowReturn("borrow"); }
async function confirmReturn() { if (!state.returnReader) return setStatus("请先选择读者", "error"); await mutate("/api/return", {reader_id:state.returnReader.id, barcodes:state.returnBarcodes}); state.returnBarcodes=[]; renderStage("returnStageTable", []); await searchBorrowReturn("return"); }
async function saveSettings() { await mutate("/api/settings", {default_borrow_days:value("defaultBorrowDays"), renewal_days:value("renewalDays")}); await refreshLookups(); }
function clearShelfForm() { state.selectedShelf = null; $("newShelf").value=""; $("newShelfNote").value=""; }
function clearReasonForm() { state.selectedReason = null; $("newReason").value=""; }
async function addShelf() { await mutate("/api/shelves", {name:value("newShelf"), note:value("newShelfNote")}); clearShelfForm(); await refreshLookups(); }
async function updateShelf() {
  if (!state.selectedShelf) return setStatus("请选择要修改的书架", "error");
  await mutate("/api/shelves/" + state.selectedShelf.id, {name:value("newShelf"), note:value("newShelfNote")}, "PUT");
  clearShelfForm(); await refreshLookups();
}
async function deleteShelf() {
  if (!state.selectedShelf) return setStatus("请选择要删除的书架", "error");
  if (!confirm("确定删除选中的书架？历史库存和借阅记录会保留。")) return;
  await mutate("/api/shelves/" + state.selectedShelf.id, {}, "DELETE");
  clearShelfForm(); await refreshLookups(); await loadInventory();
}
async function addReason() { await mutate("/api/reasons", {category:value("reasonCategory"), name:value("newReason")}); clearReasonForm(); await refreshLookups(); }
async function updateReason() {
  if (!state.selectedReason) return setStatus("请选择要修改的原因", "error");
  await mutate("/api/reasons/" + state.selectedReason.id, {category:value("reasonCategory"), name:value("newReason")}, "PUT");
  clearReasonForm(); await refreshLookups();
}
async function deleteReason() {
  if (!state.selectedReason) return setStatus("请选择要删除的原因", "error");
  if (!confirm("确定删除选中的原因？历史日志会保留。")) return;
  await mutate("/api/reasons/" + state.selectedReason.id, {}, "DELETE");
  clearReasonForm(); await refreshLookups();
}
function renderSettingsTables() {
  renderTable("shelfTable", [["name","书架名称"],["note","备注"]], state.shelves, row => {
    state.selectedShelf = row; $("newShelf").value = row.name; $("newShelfNote").value = row.note;
  });
  const allReasons = Object.entries(state.reasons).flatMap(([category, rows]) => rows.map(r => ({...r, category})));
  renderTable("reasonTable", [["category","分类"],["name","原因"]], allReasons, row => {
    state.selectedReason = row; $("reasonCategory").value = row.category; $("newReason").value = row.name;
  });
}
window.onerror = (msg) => setStatus(String(msg), "error");
(async function init(){
  try { setupTabs(); await loadInitial(); await loadDashboard(); await loadInventory(); await loadReaders(); }
  catch (err) { setStatus(err.message, "error"); console.error(err); }
})();
</script>
</body>
</html>
"""


def open_browser_later(url: str) -> None:
    def runner() -> None:
        time.sleep(0.4)
        webbrowser.open(url)

    threading.Thread(target=runner, daemon=True).start()


def main() -> None:
    log("准备启动本地 Web 管理界面")
    port = find_port()
    app = WebLibraryApp()
    handler = app.make_handler()
    server = HTTPServer(("127.0.0.1", port), handler)
    url = f"http://127.0.0.1:{port}/"
    log(f"服务已启动：{url}")
    log("数据文件：" + str(DEFAULT_DB_PATH))
    open_browser_later(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("收到退出请求")
    finally:
        server.server_close()
        log("服务已关闭")
