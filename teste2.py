"""
PVD Dos Mosqueteiros - Versão Aprimorada v3 (ajustado)
Impressão: agora usa spooler do Windows (win32print) quando preferida = windows.
Uso: python teste2_fixed.py
"""

import os
import io
import json
import csv
import sqlite3
import threading
import platform
from datetime import datetime
from tkinter import (Tk, Toplevel, Label, Entry, Button, StringVar, IntVar,
                     filedialog, messagebox, Menu)
from tkinter import ttk
from PIL import Image, ImageTk

# Impressão ESC/POS (opcional)
try:
    from escpos.printer import Usb, Serial, Windows as EscposWindows
except Exception:
    Usb = None
    Serial = None
    EscposWindows = None

# USB / serial enum (pyusb)
try:
    import usb.core
    import usb.util
except Exception:
    usb = None

import serial.tools.list_ports

# Suporte ao Spooler do Windows
try:
    if platform.system() == "Windows":
        import win32print
        import win32ui  # not strictly required but imported for completeness
        WIN32 = True
    else:
        win32print = None
        WIN32 = False
except Exception:
    win32print = None
    WIN32 = False

# Optional postgres
try:
    import psycopg2
    PSYCOPG2 = True
except Exception:
    PSYCOPG2 = False

# Optional pandas for report convenience
try:
    import pandas as pd
    PANDAS = True
except Exception:
    PANDAS = False

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "pvd_mosqueteiros.db")
IMAGES_DIR = os.path.join(BASE_DIR, "product_images")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
os.makedirs(IMAGES_DIR, exist_ok=True)

# --- DATABASE SCHEMA (SQLite) ---
SQLITE_SCHEMA = r"""
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sku TEXT UNIQUE,
    name TEXT NOT NULL,
    price REAL NOT NULL,
    image_path TEXT
);

CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    phone TEXT,
    house_number TEXT,
    address TEXT
);

CREATE TABLE IF NOT EXISTS sales (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER,
    total REAL,
    payment_method TEXT,
    discount REAL DEFAULT 0,
    created_at TEXT,
    FOREIGN KEY(customer_id) REFERENCES customers(id)
);

CREATE TABLE IF NOT EXISTS sale_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sale_id INTEGER,
    product_id INTEGER,
    qty INTEGER,
    price REAL,
    discount REAL DEFAULT 0,
    FOREIGN KEY(sale_id) REFERENCES sales(id),
    FOREIGN KEY(product_id) REFERENCES products(id)
);
"""

# --- HELPERS: DB ---
def init_db(path=DB_PATH):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.executescript(SQLITE_SCHEMA)
    conn.commit()
    return conn

# --- CONFIG ---
def load_config():
    if not os.path.exists(CONFIG_PATH):
        return {}
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}

def save_config(cfg: dict):
    try:
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print('Erro ao salvar config:', e)

# --- HELPERS: Printer detection ---
def find_usb_devices():
    devices = []
    if usb is None:
        return devices
    try:
        for d in usb.core.find(find_all=True):
            # some devices don't expose idVendor / idProduct directly
            try:
                vid = int(d.idVendor)
                pid = int(d.idProduct)
                devices.append((vid, pid))
            except Exception:
                pass
    except Exception:
        pass
    return devices

def list_serial_ports():
    try:
        return [p.device for p in serial.tools.list_ports.comports()]
    except Exception:
        return []

def try_create_printer(vid=None, pid=None, port=None, port_name=None):
    """Tenta criar um objeto printer (Usb, Serial ou Windows escpos)."""
    if vid and pid and Usb is not None:
        try:
            # usb expects ints
            return Usb(int(vid), int(pid), timeout=0)
        except Exception:
            pass
    if port and Serial is not None:
        try:
            return Serial(port=port, baudrate=9600, timeout=1)
        except Exception:
            pass
    if port_name and EscposWindows is not None:
        try:
            return EscposWindows(printer_name=port_name)
        except Exception as e:
            print(f"Falha ao conectar via Escpos Windows Spooler ({port_name}): {e}")
            pass
    return None

# --- ESC/POS bitmap helper (imagem->bitmap para impressora) ---
def escpos_print_image(printer_obj, pil_image):
    if not hasattr(printer_obj, 'image'):
        return False
    try:
        w = 384
        pil_image = pil_image.convert('L')
        ratio = w / pil_image.width
        h = int(pil_image.height * ratio)
        img_resized = pil_image.resize((w, h))
        printer_obj.image(img_resized)
        return True
    except Exception:
        return False

# --- PRINT RECEIPT (unified: Windows spooler OR escpos) ---
def print_receipt(printer_obj, sale_info, customer, items, logo_path="logo_print"):
    """
    Cupom formatado padrão Pub Mosqueteiros
    """
    loja_nome = "PUB MOSQUETEIROS"
    loja_end = "Rua Ana Vagos Pereira, 315 - Suzano, SP"
    loja_tel = "Tel/WhatsApp: (11) 91678-9928"

    numero_pedido = sale_info.get("id", 0)
    desconto = sale_info.get("discount", 0)
    total = sale_info.get("total", 0)
    forma_pagamento = sale_info.get("payment_method", "-")

    nome_cliente = customer.get("name", "-")
    endereco = customer.get("address", "-")
    numero_casa = customer.get("house_number", "-")
    telefone = customer.get("phone", "-")

    # ---------------------------
    # IMPRESSORA WINDOWS
    # ---------------------------
    if isinstance(printer_obj, dict) and printer_obj.get('type') == 'windows':
        printer_name = printer_obj.get('name', win32print.GetDefaultPrinter())

        texto = []
        texto.append("-----------------------------------------")
        texto.append(f"                 PEDIDO: {numero_pedido:03d}")
        texto.append(f"               Cliente: {nome_cliente}")
        texto.append(f"              Endereço: {endereco}")
        texto.append(f"                Número: {numero_casa}")
        texto.append(f"              Telefone: {telefone}")
        texto.append("-----------------------------------------")
        texto.append(loja_nome)
        texto.append(loja_end)
        texto.append(loja_tel)
        texto.append("-----------------------------------------")
        texto.append("ITEMS:")
        for it in items:
            nome = it["name"]
            qtd = it["qty"]
            preco = it["price"]
            sub = qtd * preco
            texto.append(f"{nome:<25} {qtd}x {preco:.2f}")
            texto.append(f"   Sub: {sub:.2f}")
        texto.append("-----------------------------------------")
        texto.append(f"Desconto: R$ {desconto:.2f}")
        texto.append(f"Total: R$ {total:.2f}")
        texto.append(f"Pagamento: {forma_pagamento}")
        texto.append("-----------------------------------------")
        texto.append("")
        texto.append("Siga a gente no Instagram")
        texto.append("@pub_mosqueteiros")
        texto.append("")
        texto.append("Muito Obrigado!!")
        texto_final = "\n".join(texto) + "\n\n\n"

        hPrinter = win32print.OpenPrinter(printer_name)
        try:
            hJob = win32print.StartDocPrinter(hPrinter, 1, ("PDV - Recibo", None, "RAW"))
            win32print.StartPagePrinter(hPrinter)
            win32print.WritePrinter(hPrinter, texto_final.encode('cp1252', errors='replace'))
            win32print.EndPagePrinter(hPrinter)
            win32print.EndDocPrinter(hPrinter)
        finally:
            win32print.ClosePrinter(hPrinter)
        return True

    # ---------------------------
    # IMPRESSORA ESC/POS
    # ---------------------------
    try:
        p = printer_obj

        # IMPRIMIR LOGO (se existir)
        if logo_path and os.path.exists(logo_path):
            logo = Image.open(logo_path).convert("L")
            width = 384  # largura padrão da impressora térmica
            ratio = width / logo.width
            logo = logo.resize((width, int(logo.height * ratio)))
            p.image(logo)

        # Pedido em destaque
        p.set(align="center", bold=True, width=2, height=2)
        p.text(f"PEDIDO: {numero_pedido:03d}\n")

        # Cliente e endereço
        p.set(width=1, height=1, bold=False)
        p.text(f"Cliente: {nome_cliente}\n")
        p.text(f"Endereço: {endereco}\n")
        p.text(f"Número: {numero_casa}\n")
        p.text(f"Telefone: {telefone}\n")
        p.text("-----------------------------------------\n")

        # Informações da loja
        p.set(align="center", bold=True)
        p.text(loja_nome + "\n")
        p.set(bold=False)
        p.text(loja_end + "\n")
        p.text(loja_tel + "\n")
        p.text("-----------------------------------------\n")

        # Itens
        p.text("ITEMS:\n")
        for it in items:
            nome = it["name"]
            qtd = it["qty"]
            preco = it["price"]
            sub = qtd * preco
            p.text(f"{nome:<25} {qtd}x {preco:.2f}\n")
            p.text(f"   Sub: {sub:.2f}\n")

        p.text("-----------------------------------------\n")
        p.text(f"Desconto: R$ {desconto:.2f}\n")
        p.text(f"Total: R$ {total:.2f}\n")
        p.text(f"Pagamento: {forma_pagamento}\n")
        p.text("-----------------------------------------\n\n")

        # QR CODE DO INSTAGRAM
        if os.path.exists("qr_print.png"):
            p.image("qr_print.png")

        p.set(align="center", bold=True)
        p.text("\nSiga a gente no Instagram\n")
        p.text("@pub_mosqueteiros\n\n")

        p.text("Muito Obrigado!!\n\n")
        p.cut()

        return True

    except Exception as e:
        raise RuntimeError(f"Erro ao imprimir via ESC/POS: {e}")


        # fallback: escpos object printing
        if not hasattr(printer_obj, 'text'):
            raise RuntimeError("Objeto de impressora inválido para método ESC/POS.")
        try:
            try:
                printer_obj.set(align='center', bold=True)
            except Exception:
                pass
            if logo_path and os.path.exists(logo_path):
                try:
                    logo = Image.open(logo_path)
                    escpos_print_image(printer_obj, logo)
                except Exception:
                    pass
            try:
                printer_obj.text('Pub Mosqueteiros\n')
                printer_obj.set(align='left', bold=False)
                printer_obj.text(f"Venda: {sale_info['id']}    Data: {sale_info['created_at']}\n")
                printer_obj.text(f"Cliente: {customer.get('name','-')}\n")
                printer_obj.text(f"Tel: {customer.get('phone','-')}  End: {customer.get('address','-')}\n")
                printer_obj.text('-' * 32 + '\n')
                for it in items:
                    name = it['name'][:20]
                    qty = it['qty']
                    price = it['price']
                    subtotal = it['subtotal']
                    printer_obj.text(f"{name:20} {qty:>3} x {price:.2f}\n")
                    if it.get('discount',0):
                        printer_obj.text(f"    Desc item: R$ {it['discount']:.2f}\n")
                    printer_obj.text(f"    SUB: {subtotal:.2f}\n")
                printer_obj.text('-' * 32 + '\n')
                try:
                    printer_obj.set(align='right', bold=True)
                except Exception:
                    pass
                printer_obj.text(f"DESCONTO: R$ {sale_info.get('discount',0):.2f}\n")
                printer_obj.text(f"TOTAL: R$ {sale_info['total']:.2f}\n")
                printer_obj.set(align='center')
                printer_obj.text(f"Pagamento: {sale_info.get('payment_method','-')}\n")
                printer_obj.text('\nObrigado!\n\n')
            except Exception:
                pass
            try:
                printer_obj.cut()
            except Exception:
                pass
            return True
        except Exception:
            return False
    except Exception as e:
        # re-raise for debug in app
        raise

# --- APP ---
class PVDApp:
    def __init__(self, root):
        self.root = root
        root.title('PVD Dos Mosqueteiros - Aprimorado v3')
        root.geometry('1200x740')

        # DB
        self.conn = init_db()
        self.cur = self.conn.cursor()

        # Data
        self.products = []
        self.cart = []
        self.preview_cache = {}
        self.detected_printers = []
        self.preferred_printer = None
        self.logo_path = None
        self.config = load_config()
        if isinstance(self.config, dict):
            self.preferred_printer = self.config.get('preferred_printer')

        # ttk style
        style = ttk.Style()
        try:
            style.theme_use('clam')
        except Exception:
            pass

        self.create_ui()
        self.load_products()
        self.refresh_products()

        # background detect printers
        threading.Thread(target=self.detect_printers_background, daemon=True).start()

    def create_ui(self):
        topbar = ttk.Frame(self.root)
        topbar.pack(fill='x')
        title = ttk.Label(topbar, text='PVD Dos Mosqueteiros', font=('Arial', 14, 'bold'))
        title.pack(side='left', padx=8, pady=6)
        # Config button (gear)
        cfg_btn = ttk.Button(topbar, text='⚙️ Configurações', command=self.open_settings)
        cfg_btn.pack(side='right', padx=8)

        nb = ttk.Notebook(self.root)
        nb.pack(fill='both', expand=True)

        frame_pos = ttk.Frame(nb)
        frame_admin = ttk.Frame(nb)
        nb.add(frame_pos, text='PDV')
        nb.add(frame_admin, text='Admin / Relatórios')

        # --- POS FRAME (grid) ---
        left = ttk.Frame(frame_pos, padding=8)
        left.grid(row=0, column=0, sticky='nsw')

        ttk.Label(left, text='Produtos').pack(anchor='w')
        self.products_tree = ttk.Treeview(left, columns=('price','sku'), show='headings', height=20)
        self.products_tree.heading('price', text='Preço')
        self.products_tree.heading('sku', text='SKU')
        self.products_tree.pack()
        self.products_tree.bind('<Double-1>', lambda e: self.add_product_to_cart())

        btns = ttk.Frame(left)
        btns.pack(pady=6)
        ttk.Button(btns, text='Adicionar produto', command=self.open_add_product).pack(side='left')
        ttk.Button(btns, text='Editar produto', command=self.open_edit_product).pack(side='left')
        ttk.Button(btns, text='Importar imagem', command=self.import_image_for_selected).pack(side='left')

        # Center - cart and customer
        center = ttk.Frame(frame_pos, padding=8)
        center.grid(row=0, column=1, sticky='nsew')
        frame_pos.columnconfigure(1, weight=1)

        ttk.Label(center, text='Carrinho').pack(anchor='w')
        cart_area = ttk.Frame(center)
        cart_area.pack(fill='both', expand=False)

        self.cart_tree = ttk.Treeview(cart_area, columns=('qty','price','subtotal'), show='headings', height=10)
        for c in ('qty','price','subtotal'):
            self.cart_tree.heading(c, text=c.capitalize())
        self.cart_tree.pack(side='left', fill='x', expand=True)

        # Bindings: double click to edit qty
        self.cart_tree.bind('<Double-1>', self.on_cart_double_click)
        # Right click menu
        self.cart_tree.bind('<Button-3>', self.on_cart_right_click)

        # Scrollbar
        sb = ttk.Scrollbar(cart_area, orient='vertical', command=self.cart_tree.yview)
        sb.pack(side='left', fill='y')
        self.cart_tree.configure(yscrollcommand=sb.set)

        # +/- and remove buttons
        cart_controls = ttk.Frame(center)
        cart_controls.pack(fill='x', pady=6)
        ttk.Button(cart_controls, text='+', width=3, command=lambda: self.change_selected_qty(1)).pack(side='left')
        ttk.Button(cart_controls, text='-', width=3, command=lambda: self.change_selected_qty(-1)).pack(side='left')
        ttk.Button(cart_controls, text='Remover item', command=self.remove_selected_cart).pack(side='left', padx=6)
        ttk.Button(cart_controls, text='Limpar carrinho', command=self.clear_cart).pack(side='left')

        # Customer
        cust_frame = ttk.LabelFrame(center, text='Cliente')
        cust_frame.pack(fill='x', pady=6)
        ttk.Label(cust_frame, text='Nome').grid(row=0,column=0)
        self.entry_name = ttk.Entry(cust_frame, width=40); self.entry_name.grid(row=0,column=1)
        ttk.Label(cust_frame, text='Telefone').grid(row=1,column=0)
        self.entry_phone = ttk.Entry(cust_frame, width=40); self.entry_phone.grid(row=1,column=1)
        ttk.Label(cust_frame, text='Nº Casa').grid(row=2,column=0)
        self.entry_house = ttk.Entry(cust_frame, width=40); self.entry_house.grid(row=2,column=1)
        ttk.Label(cust_frame, text='Endereço').grid(row=3,column=0)
        self.entry_address = ttk.Entry(cust_frame, width=40); self.entry_address.grid(row=3,column=1)

        # Discounts & payment
        misc_frame = ttk.Frame(center)
        misc_frame.pack(fill='x', pady=6)
        ttk.Label(misc_frame, text='Desconto global (R$)').pack(side='left')
        self.global_discount_var = StringVar(value='0')
        ttk.Entry(misc_frame, width=8, textvariable=self.global_discount_var).pack(side='left', padx=6)
        ttk.Label(misc_frame, text='Pagamento').pack(side='left', padx=(12,0))
        self.payment_var = StringVar(value='Dinheiro')
        ttk.Combobox(misc_frame, values=['Dinheiro','Cartao','PIX','Outros'], textvariable=self.payment_var, width=12).pack(side='left')

        # Right - preview, totals, printer
        right = ttk.Frame(frame_pos, padding=8)
        right.grid(row=0, column=2, sticky='nse')

        ttk.Label(right, text='Preview').pack()
        self.preview_label = ttk.Label(right, text='Sem imagem', relief='sunken', width=30)
        self.preview_label.pack()

        self.total_var = StringVar(value='Total: R$ 0.00')
        ttk.Label(right, textvariable=self.total_var, font=('Arial',14)).pack(pady=6)

        ttk.Button(right, text='Salvar Venda', command=self.save_sale).pack(fill='x')
        # Impressão: usa impressora memorizada quando disponível
        ttk.Button(right, text='Imprimir (selecionar impressora)', command=self.print_with_selection).pack(fill='x', pady=4)
        ttk.Button(right, text='Exportar vendas (CSV)', command=self.export_sales_csv).pack(fill='x', pady=4)

        # Printer management
        printer_frame = ttk.LabelFrame(right, text='Impressoras')
        printer_frame.pack(fill='x', pady=6)
        self.printers_list = ttk.Treeview(printer_frame, columns=('type','id'), show='headings', height=4)
        self.printers_list.heading('type', text='Tipo')
        self.printers_list.heading('id', text='ID')
        self.printers_list.pack()
        ttk.Button(printer_frame, text='Detectar Impressoras', command=self.detect_printers_manual).pack(pady=4)
        ttk.Button(printer_frame, text='Salvar preferida', command=self.save_preferred_printer).pack()

        # Admin tab
        admin_top = ttk.Frame(frame_admin, padding=8)
        admin_top.pack(fill='both', expand=True)
        ttk.Button(admin_top, text='Ver histórico de vendas', command=self.open_sales_history).pack()
        ttk.Button(admin_top, text='Configurar PostgreSQL (opcional)', command=self.open_postgres_config).pack(pady=6)
        ttk.Button(admin_top, text='Definir logo (para impressão)', command=self.choose_logo).pack(pady=6)

        # status bar
        self.status_var = StringVar(value='Pronto')
        ttk.Label(self.root, textvariable=self.status_var, anchor='w').pack(side='bottom', fill='x')

    # --- PRODUCTS ---
    def load_products(self):
        self.cur.execute('SELECT id, sku, name, price, image_path FROM products ORDER BY name')
        rows = self.cur.fetchall()
        self.products = [dict(r) for r in rows]

    def refresh_products(self):
        for i in self.products_tree.get_children():
            self.products_tree.delete(i)
        for p in self.products:
            self.products_tree.insert('', 'end', iid=p['id'], values=(f"R$ {p['price']:.2f}", p['sku'] or ''))

    def open_add_product(self):
        d = Toplevel(self.root)
        d.title('Adicionar produto')
        ttk.Label(d, text='SKU').grid(row=0,column=0)
        sku = ttk.Entry(d); sku.grid(row=0,column=1)
        ttk.Label(d, text='Nome').grid(row=1,column=0)
        name = ttk.Entry(d); name.grid(row=1,column=1)
        ttk.Label(d, text='Preço').grid(row=2,column=0)
        price = ttk.Entry(d); price.grid(row=2,column=1)
        ttk.Label(d, text='Imagem (opcional)').grid(row=3,column=0)
        img = ttk.Entry(d); img.grid(row=3,column=1)

        def choose():
            p = filedialog.askopenfilename(filetypes=[('Imagens','*.png;*.jpg;*.jpeg')])
            if p:
                img.delete(0,'end')
                img.insert(0,p)

        ttk.Button(d, text='Selecionar imagem', command=choose).grid(row=3,column=2)

        def add():
            try:
                pr = float(price.get())
            except Exception:
                pr = 0.0
            image_path = None
            if img.get():
                try:
                    src = img.get()
                    dest = os.path.join(IMAGES_DIR, f"{int(datetime.now().timestamp())}_{os.path.basename(src)}")
                    with open(src,'rb') as rf, open(dest,'wb') as wf:
                        wf.write(rf.read())
                    image_path = dest
                except Exception as e:
                    messagebox.showwarning('Aviso','Erro ao copiar imagem: '+str(e))
            self.cur.execute('INSERT INTO products (sku,name,price,image_path) VALUES (?,?,?,?)', (sku.get() or None, name.get(), pr, image_path))
            self.conn.commit()
            self.load_products()
            self.refresh_products()
            d.destroy()

        ttk.Button(d, text='Adicionar', command=add).grid(row=4,column=1)

    def open_edit_product(self):
        sel = self.products_tree.selection()
        if not sel:
            messagebox.showinfo('Info','Selecione um produto para editar')
            return
        pid = int(sel[0])
        self.cur.execute('SELECT id,sku,name,price,image_path FROM products WHERE id=?', (pid,))
        row = self.cur.fetchone()
        if not row:
            messagebox.showerror('Erro','Produto não encontrado')
            return
        d = Toplevel(self.root)
        d.title('Editar produto')
        ttk.Label(d, text='SKU').grid(row=0,column=0)
        sku = ttk.Entry(d); sku.grid(row=0,column=1); sku.insert(0, row['sku'] or '')
        ttk.Label(d, text='Nome').grid(row=1,column=0)
        name = ttk.Entry(d); name.grid(row=1,column=1); name.insert(0, row['name'])
        ttk.Label(d, text='Preço').grid(row=2,column=0)
        price = ttk.Entry(d); price.grid(row=2,column=1); price.insert(0, f"{row['price']:.2f}")
        ttk.Label(d, text='Imagem (opcional)').grid(row=3,column=0)
        img = ttk.Entry(d); img.grid(row=3,column=1); img.insert(0, row['image_path'] or '')

        def choose():
            p = filedialog.askopenfilename(filetypes=[('Imagens','*.png;*.jpg;*.jpeg')])
            if p:
                img.delete(0,'end')
                img.insert(0,p)

        ttk.Button(d, text='Selecionar imagem', command=choose).grid(row=3,column=2)

        def save():
            try:
                pr = float(price.get())
            except Exception:
                pr = 0.0
            image_path = None
            if img.get():
                try:
                    src = img.get()
                    if os.path.exists(src) and os.path.dirname(src) != IMAGES_DIR:
                        dest = os.path.join(IMAGES_DIR, f"{int(datetime.now().timestamp())}_{os.path.basename(src)}")
                        with open(src,'rb') as rf, open(dest,'wb') as wf:
                            wf.write(rf.read())
                        image_path = dest
                    else:
                        image_path = src
                except Exception as e:
                    messagebox.showwarning('Aviso','Erro ao copiar imagem: '+str(e))
            self.cur.execute('UPDATE products SET sku=?, name=?, price=?, image_path=? WHERE id=?', (sku.get() or None, name.get(), pr, image_path, pid))
            self.conn.commit()
            self.load_products(); self.refresh_products()
            d.destroy()

        ttk.Button(d, text='Salvar', command=save).grid(row=4,column=1)

    def import_image_for_selected(self):
        sel = self.products_tree.selection()
        if not sel:
            messagebox.showinfo('Info','Selecione um produto na lista')
            return
        pid = int(sel[0])
        path = filedialog.askopenfilename(filetypes=[('Imagens','*.png;*.jpg;*.jpeg')])
        if not path:
            return
        dest = os.path.join(IMAGES_DIR, f"{int(datetime.now().timestamp())}_{os.path.basename(path)}")
        with open(path,'rb') as rf, open(dest,'wb') as wf:
            wf.write(rf.read())
        self.cur.execute('UPDATE products SET image_path=? WHERE id=?', (dest, pid))
        self.conn.commit()
        self.load_products(); self.refresh_products()
        self.set_status('Imagem adicionada ao produto')

    # --- CART ---
    def add_product_to_cart(self):
        sel = self.products_tree.selection()
        if not sel:
            return
        pid = int(sel[0])
        p = next((x for x in self.products if x['id']==pid), None)
        if p is None:
            return
        it = next((x for x in self.cart if x['product_id']==pid), None)
        if it:
            it['qty'] += 1
        else:
            self.cart.append({'product_id':pid, 'name':p['name'], 'price':p['price'], 'qty':1, 'image':p['image_path'], 'discount':0.0})
        self.refresh_cart()
        self.update_preview(p)

    def refresh_cart(self):
        for i in self.cart_tree.get_children():
            self.cart_tree.delete(i)
        total = 0.0
        for idx, it in enumerate(self.cart):
            subtotal = it['qty']*it['price'] - it.get('discount',0)
            total += subtotal
            self.cart_tree.insert('', 'end', iid=str(idx), values=(it['qty'], f"R$ {it['price']:.2f}", f"R$ {subtotal:.2f}"))
        global_disc = 0.0
        try:
            global_disc = float(self.global_discount_var.get() or 0)
        except Exception:
            global_disc = 0.0
        total_after = max(0.0, total - global_disc)
        self.total_var.set(f"Total: R$ {total_after:.2f}")

    def remove_selected_cart(self):
        sel = self.cart_tree.selection()
        if not sel:
            messagebox.showinfo('Info','Selecione um item para remover')
            return
        idx = int(sel[0])
        if 0 <= idx < len(self.cart):
            del self.cart[idx]
        self.refresh_cart()

    def clear_cart(self):
        self.cart = []
        self.refresh_cart()

    def change_selected_qty(self, delta):
        sel = self.cart_tree.selection()
        if not sel:
            messagebox.showinfo('Info','Selecione um item para ajustar')
            return
        idx = int(sel[0])
        if 0 <= idx < len(self.cart):
            self.cart[idx]['qty'] = max(1, self.cart[idx]['qty'] + delta)
        self.refresh_cart()

    def on_cart_double_click(self, event=None):
        sel = self.cart_tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        if not (0 <= idx < len(self.cart)):
            return
        it = self.cart[idx]
        d = Toplevel(self.root)
        d.title('Editar quantidade')
        ttk.Label(d, text=f"Produto: {it['name']}").grid(row=0,column=0,columnspan=2)
        ttk.Label(d, text='Quantidade').grid(row=1,column=0)
        qty_e = ttk.Entry(d); qty_e.grid(row=1,column=1); qty_e.insert(0, str(it['qty']))
        def save():
            try:
                q = int(qty_e.get())
                if q < 1:
                    raise ValueError()
            except Exception:
                messagebox.showerror('Erro','Quantidade inválida')
                return
            it['qty'] = q
            self.refresh_cart()
            d.destroy()
        ttk.Button(d, text='Salvar', command=save).grid(row=2,column=0,columnspan=2)

    def on_cart_right_click(self, event):
        iid = self.cart_tree.identify_row(event.y)
        if not iid:
            return
        self.cart_tree.selection_set(iid)
        menu = Menu(self.root, tearoff=0)
        menu.add_command(label='Remover item', command=self.remove_selected_cart)
        menu.add_command(label='Editar quantidade', command=lambda: self.on_cart_double_click())
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def update_preview(self, product):
        path = product.get('image_path') or product.get('image')
        if path and os.path.exists(path):
            img = Image.open(path); img.thumbnail((250,250))
            tkimg = ImageTk.PhotoImage(img)
            self.preview_cache['img'] = tkimg
            self.preview_label.config(image=tkimg, text='')
        else:
            self.preview_label.config(image='', text=product.get('name','Sem imagem'))

    # --- SALES ---
    def save_sale(self):
        if not self.cart:
            self.set_status('Carrinho vazio')
            return None
        name = self.entry_name.get().strip()
        phone = self.entry_phone.get().strip()
        house = self.entry_house.get().strip()
        addr = self.entry_address.get().strip()
        self.cur.execute('INSERT INTO customers (name,phone,house_number,address) VALUES (?,?,?,?)', (name,phone,house,addr))
        cid = self.cur.lastrowid
        total = sum(it['qty']*it['price'] - it.get('discount',0) for it in self.cart)
        try:
            global_disc = float(self.global_discount_var.get() or 0)
        except Exception:
            global_disc = 0.0
        total_after = max(0.0, total - global_disc)
        created_at = datetime.now().isoformat(sep=' ', timespec='seconds')
        payment = self.payment_var.get()
        self.cur.execute('INSERT INTO sales (customer_id,total,payment_method,discount,created_at) VALUES (?,?,?,?,?)', (cid, total_after, payment, global_disc, created_at))
        sale_id = self.cur.lastrowid
        for it in self.cart:
            self.cur.execute('INSERT INTO sale_items (sale_id,product_id,qty,price,discount) VALUES (?,?,?,?,?)', (sale_id, it['product_id'], it['qty'], it['price'], it.get('discount',0)))
        self.conn.commit()
        self.set_status(f'Venda salva (id {sale_id})')
        return sale_id

    def print_with_selection(self):
        if not self.cart:
            messagebox.showinfo('Info','Carrinho vazio')
            return
        # Se já temos impressora preferida, tenta imprimir direto
        pref = self.preferred_printer
        if pref:
            p = None
            try:
                if pref.get('type') == 'usb':
                    p = try_create_printer(vid=pref.get('vid'), pid=pref.get('pid'))
                elif pref.get('type') == 'serial':
                    p = try_create_printer(port=pref.get('port'))
                elif pref.get('type') == 'windows':
                    # For windows, we pass the dict to print_receipt which will use win32print
                    p = pref
            except Exception:
                p = None

            if p is not None:
                # imprime sem pedir novamente
                sale_id = self.save_sale()
                if sale_id is None:
                    return
                sale_total = sum(it['qty']*it['price'] - it.get('discount',0) for it in self.cart)
                try:
                    g_disc = float(self.global_discount_var.get() or 0)
                except Exception:
                    g_disc = 0.0
                sale_info = {
                    'id': sale_id,
                    'created_at': datetime.now().isoformat(sep=' ', timespec='seconds'),
                    'total': max(0.0, sale_total - g_disc),
                    'discount': g_disc,
                    'payment_method': self.payment_var.get()
                }
                cust = {'name': self.entry_name.get(), 'phone': self.entry_phone.get(), 'address': self.entry_address.get()}
                items = []
                for it in self.cart:
                    items.append({'name': it['name'], 'qty': it['qty'], 'price': it['price'], 'subtotal': it['qty']*it['price'] - it.get('discount',0), 'discount': it.get('discount',0)})
                try:
                    print_receipt(p, sale_info, cust, items, logo_path=self.logo_path)
                    self.clear_cart()
                    self.set_status('Impressão enviada (preferida)')
                    return
                except Exception as e:
                    print('Erro impressão preferida:', e)
                    self.set_status('Falha com impressora preferida, selecione outra')

        # Se chegamos aqui, não há impressora preferida funcional -> abrir seleção única
        self.open_printer_selection_and_print()

    def open_printer_selection_and_print(self):
        sel = Toplevel(self.root)
        sel.title('Selecionar impressora')
        sel.geometry('420x300')
        ttk.Label(sel, text='Detectadas:').pack()
        tree = ttk.Treeview(sel, columns=('type','id'), show='headings', height=8)
        tree.heading('type', text='Tipo'); tree.heading('id', text='ID')
        tree.pack(fill='both', expand=True)
        for i,p in enumerate(self.detected_printers):
            if p.get('type')=='usb':
                tid = f"VID:{p.get('vid',0):04x} PID:{p.get('pid',0):04x}"
            elif p.get('type') == 'serial':
                tid = p.get('port')
            elif p.get('type') == 'windows':
                tid = p.get('name')
            else:
                tid = 'Desconhecido'
            tree.insert('', 'end', iid=str(i), values=(p.get('type'), tid))

        def do_print():
            s = tree.selection()
            if not s:
                messagebox.showwarning('Aviso','Selecione uma impressora')
                return
            idx = int(s[0]); info = self.detected_printers[idx]
            p = None
            if info.get('type') == 'usb':
                p = try_create_printer(vid=info.get('vid'), pid=info.get('pid'))
            elif info.get('type') == 'serial':
                p = try_create_printer(port=info.get('port'))
            elif info.get('type') == 'windows':
                p = info  # pass dict to print_receipt which will use win32print

            if p is None:
                messagebox.showerror('Erro', 'Não foi possível conectar à impressora selecionada.')
                return

            # salva escolha como preferida
            self.preferred_printer = info
            self.config['preferred_printer'] = info
            save_config(self.config)

            # salva e imprime sem confirmações
            sale_id = self.save_sale()
            if sale_id is None:
                sel.destroy(); return

            sale_total = sum(it['qty']*it['price'] - it.get('discount',0) for it in self.cart)
            try:
                g_disc = float(self.global_discount_var.get() or 0)
            except Exception:
                g_disc = 0.0
            sale_info = {
                'id': sale_id,
                'created_at': datetime.now().isoformat(sep=' ', timespec='seconds'),
                'total': max(0.0, sale_total - g_disc),
                'discount': g_disc,
                'payment_method': self.payment_var.get()
            }
            cust = {'name': self.entry_name.get(), 'phone': self.entry_phone.get(), 'address': self.entry_address.get()}
            items = []
            for it in self.cart:
                items.append({'name': it['name'], 'qty': it['qty'], 'price': it['price'], 'subtotal': it['qty']*it['price'] - it.get('discount',0), 'discount': it.get('discount',0)})
            try:
                print_receipt(p, sale_info, cust, items, logo_path=self.logo_path)
                self.clear_cart()
                sel.destroy()
                self.set_status('Impressão enviada (salva como preferida)')
            except Exception as e:
                messagebox.showerror('Erro','Falha ao imprimir: '+str(e))

        ttk.Button(sel, text='Imprimir e salvar como preferida', command=do_print).pack(pady=6)

    # --- PRINTERS ---
    def detect_printers_background(self):
        self.detected_printers = []
        try:
            devs = find_usb_devices()
            for vid,pid in devs:
                self.detected_printers.append({'type':'usb','vid':vid,'pid':pid})
        except Exception:
            pass
        try:
            ports = list_serial_ports()
            for port in ports:
                self.detected_printers.append({'type':'serial','port':port})
        except Exception:
            pass
        if WIN32:
            try:
                printers = win32print.EnumPrinters(win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS)
                for p in printers:
                    printer_name = p[2]
                    if not any(d.get('name') == printer_name for d in self.detected_printers):
                        self.detected_printers.append({'type': 'windows', 'name': printer_name})
            except Exception as e:
                print(f"Erro ao detectar impressoras Windows: {e}")

        self.set_status(f'Impressoras detectadas: {len(self.detected_printers)}')
        # refresh UI after short delay
        try:
            self.root.after(100, self.refresh_printers_ui)
        except Exception:
            pass

    def detect_printers_manual(self):
        self.set_status('Detectando impressoras...')
        threading.Thread(target=self.detect_printers_background, daemon=True).start()

    def refresh_printers_ui(self):
        try:
            tree = self.printers_list
            for i in tree.get_children():
                tree.delete(i)
            for i,p in enumerate(self.detected_printers):
                if p.get('type')=='usb':
                    tid = f"VID:{p.get('vid',0):04x} PID:{p.get('pid',0):04x}"
                    label = "ESC/POS USB"
                elif p.get('type') == 'serial':
                    tid = p.get('port')
                    label = "Porta Serial"
                elif p.get('type') == 'windows':
                    tid = p.get('name')
                    label = "Windows Spooler"
                else:
                    tid = 'Desconhecido'
                    label = "Desconhecido"
                tree.insert('', 'end', iid=str(i), values=(label, tid))
        except Exception as e:
            print(f"Erro ao atualizar UI da impressora: {e}")

    def save_preferred_printer(self):
        sel = self.printers_list.selection()
        if not sel:
            messagebox.showinfo('Info','Selecione uma impressora na lista')
            return
        idx = int(sel[0]); p = self.detected_printers[idx]
        self.preferred_printer = p
        self.config['preferred_printer'] = p
        save_config(self.config)
        self.set_status('Impressora preferida salva')

    # --- SETTINGS / CONFIGURATION ---
    def open_settings(self):
        d = Toplevel(self.root)
        d.title('Configurações')
        d.geometry('400x200')
        ttk.Label(d, text='Impressora preferida atual:').pack(anchor='w', padx=8, pady=(8,0))
        cur_txt = str(self.preferred_printer) if self.preferred_printer else 'Nenhuma'
        lbl = ttk.Label(d, text=cur_txt, relief='sunken')
        lbl.pack(fill='x', padx=8, pady=4)
        ttk.Button(d, text='Trocar impressora', command=lambda: [d.destroy(), self.open_printer_selection_dialog()]).pack(pady=6)
        ttk.Button(d, text='Limpar impressora preferida', command=self.clear_preferred_printer).pack(pady=6)

    def clear_preferred_printer(self):
        self.preferred_printer = None
        if 'preferred_printer' in self.config:
            del self.config['preferred_printer']
            save_config(self.config)
        self.set_status('Impressora preferida removida')

    def open_printer_selection_dialog(self):
        sel = Toplevel(self.root)
        sel.title('Selecionar impressora (configuração)')
        sel.geometry('420x300')
        ttk.Label(sel, text='Detectadas:').pack()
        tree = ttk.Treeview(sel, columns=('type','id'), show='headings', height=8)
        tree.heading('type', text='Tipo'); tree.heading('id', text='ID')
        tree.pack(fill='both', expand=True)
        for i,p in enumerate(self.detected_printers):
            if p.get('type')=='usb':
                tid = f"VID:{p.get('vid',0):04x} PID:{p.get('pid',0):04x}"
            elif p.get('type') == 'serial':
                tid = p.get('port')
            elif p.get('type') == 'windows':
                tid = p.get('name')
            else:
                tid = 'Desconhecido'
            tree.insert('', 'end', iid=str(i), values=(p.get('type'), tid))

        def save_and_close():
            s = tree.selection()
            if not s:
                messagebox.showwarning('Aviso','Selecione uma impressora')
                return
            idx = int(s[0]); info = self.detected_printers[idx]
            self.preferred_printer = info
            self.config['preferred_printer'] = info
            save_config(self.config)
            sel.destroy()
            self.set_status('Impressora preferida atualizada')

        ttk.Button(sel, text='Salvar como preferida', command=save_and_close).pack(pady=6)

    # --- EXPORT / REPORTS ---
    def export_sales_csv(self):
        path = filedialog.asksaveasfilename(defaultextension='.csv', filetypes=[('CSV','*.csv')])
        if not path:
            return
        self.cur.execute('SELECT s.id, s.created_at, s.total, s.payment_method, s.discount, c.name, c.phone FROM sales s JOIN customers c ON s.customer_id=c.id ORDER BY s.id')
        sales = self.cur.fetchall()
        with open(path,'w', newline='', encoding='utf-8') as wf:
            writer = csv.writer(wf)
            writer.writerow(['sale_id','created_at','total','payment','discount','customer_name','customer_phone'])
            for s in sales:
                writer.writerow([s['id'], s['created_at'], s['total'], s['payment_method'], s['discount'], s['name'], s['phone']])
        messagebox.showinfo('Exportar', 'CSV gerado com sucesso')

    def open_sales_history(self):
        d = Toplevel(self.root); d.title('Histórico de vendas')
        tree = ttk.Treeview(d, columns=('id','created','total','payment'), show='headings')
        for h in ('id','created','total','payment'):
            tree.heading(h, text=h)
        tree.pack(fill='both', expand=True)
        self.cur.execute('SELECT id,created_at,total,payment_method FROM sales ORDER BY id DESC LIMIT 500')
        for r in self.cur.fetchall():
            tree.insert('', 'end', values=(r['id'], r['created_at'], r['total'], r['payment_method']))

    # --- POSTGRES CONFIG (opcional) ---
    def open_postgres_config(self):
        if not PSYCOPG2:
            messagebox.showinfo('Info','psycopg2 não instalado. Instale psycopg2-binary para usar Postgres.')
            return
        d = Toplevel(self.root); d.title('Configurar Postgres')
        ttk.Label(d, text='host').grid(row=0,column=0); host = ttk.Entry(d); host.grid(row=0,column=1)
        ttk.Label(d, text='port').grid(row=1,column=0); port = ttk.Entry(d); port.grid(row=1,column=1)
        ttk.Label(d, text='db').grid(row=2,column=0); db = ttk.Entry(d); db.grid(row=2,column=1)
        ttk.Label(d, text='user').grid(row=3,column=0); user = ttk.Entry(d); user.grid(row=3,column=1)
        ttk.Label(d, text='password').grid(row=4,column=0); pwd = ttk.Entry(d, show='*'); pwd.grid(row=4,column=1)

        def test():
            try:
                conn = psycopg2.connect(host=host.get(), port=port.get(), dbname=db.get(), user=user.get(), password=pwd.get())
                conn.close()
                messagebox.showinfo('OK','Conexão bem-sucedida')
            except Exception as e:
                messagebox.showerror('Erro','Falha: '+str(e))
        ttk.Button(d, text='Testar conexão', command=test).grid(row=5,column=1)

    # --- LOGO ---
    def choose_logo(self):
        p = filedialog.askopenfilename(filetypes=[('Imagens','*.png;*.jpg;*.jpeg')])
        if p:
            self.logo_path = p
            self.set_status('Logo definida para impressão')

    # --- UTIL ---
    def set_status(self, text):
        self.status_var.set(text)


# --- RUN ---
def main():
    root = Tk()
    app = PVDApp(root)
    root.mainloop()

if __name__ == '__main__':
    main()

# --- Instruções de empacotamento (pyinstaller) ---
# pyinstaller --noconfirm --onefile --add-data "product_images;product_images" teste2_fixed.py
