import customtkinter as ctk
import sqlite3
from tkinter import messagebox
import webbrowser
import urllib.parse

# Configuración global
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("green")
DB_NAME = "qalinode_pos.db"

# ==========================================
# 1. BASE DE DATOS
# ==========================================
def inicializar_bd():
    conexion = sqlite3.connect(DB_NAME)
    cursor = conexion.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS productos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            stock_actual INTEGER NOT NULL,
            precio REAL NOT NULL,
            stock_minimo INTEGER DEFAULT 20,
            ventas_hoy INTEGER DEFAULT 0)''')
            
    cursor.execute("SELECT COUNT(*) FROM productos")
    if cursor.fetchone()[0] == 0:
        demo_data = [
            ("Amoxicilina 500mg", 142, 12.50, 20, 0), 
            ("Paracetamol 1g", 8, 2.00, 15, 0),
            ("Ibuprofeno 400mg", 85, 18.00, 25, 0)
        ]
        cursor.executemany("INSERT INTO productos (nombre, stock_actual, precio, stock_minimo, ventas_hoy) VALUES (?, ?, ?, ?, ?)", demo_data)
                           
    conexion.commit()
    conexion.close()

# ==========================================
# 2. APLICACIÓN PRINCIPAL
# ==========================================
class ChasquiLogApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Chasqui-Log - POS y Logística")
        self.geometry("1100x750")
        self.configure(fg_color="#121212")

        # Paleta de colores
        self.color_panel = "#1E1E1E"
        self.color_acento_verde = "#10B981"
        self.color_alerta = "#F59E0B"
        self.color_texto_secundario = "#A0A0A0"

        # Variables de estado
        self.carrito = {} # Diccionario: {id_producto: {'nombre': x, 'precio': y, 'cantidad': z}}
        self.total_actual = 0.0

        self.crear_navegacion_superior()
        
        self.contenedor_principal = ctk.CTkFrame(self, fg_color="transparent")
        self.contenedor_principal.pack(fill="both", expand=True, padx=20, pady=10)

        # Crear pantallas
        self.pantalla_inventario = self.crear_pantalla_inventario()
        self.pantalla_dispensacion = self.crear_pantalla_dispensacion()

        # Iniciar app
        self.mostrar_pantalla("inventario")

    # ==========================================
    # NAVEGACIÓN
    # ==========================================
    def crear_navegacion_superior(self):
        nav_frame = ctk.CTkFrame(self, fg_color=self.color_panel, height=60, corner_radius=15)
        nav_frame.pack(fill="x", padx=20, pady=(20, 0))
        
        ctk.CTkLabel(nav_frame, text="Chasqui-Log", font=("Helvetica", 20, "bold"), text_color="white").pack(side="left", padx=20, pady=15)
        
        frame_tabs = ctk.CTkFrame(nav_frame, fg_color="transparent")
        self.btn_inv = ctk.CTkButton(frame_tabs, text="INVENTARIO", fg_color="transparent", text_color=self.color_acento_verde, hover_color="#2A2A2A", command=lambda: self.mostrar_pantalla("inventario"))
        self.btn_disp = ctk.CTkButton(frame_tabs, text="DISPENSACIÓN", fg_color="transparent", text_color=self.color_texto_secundario, hover_color="#2A2A2A", command=lambda: self.mostrar_pantalla("dispensacion"))
        
        self.btn_inv.pack(side="left", padx=5)
        self.btn_disp.pack(side="left", padx=5)
        frame_tabs.place(relx=0.5, rely=0.5, anchor="center")

    def mostrar_pantalla(self, nombre):
        self.pantalla_inventario.pack_forget()
        self.pantalla_dispensacion.pack_forget()
        self.btn_inv.configure(text_color=self.color_acento_verde if nombre == "inventario" else self.color_texto_secundario)
        self.btn_disp.configure(text_color=self.color_acento_verde if nombre == "dispensacion" else self.color_texto_secundario)
        
        if nombre == "inventario":
            self.pantalla_inventario.pack(fill="both", expand=True)
            self.actualizar_lista_inventario()
        elif nombre == "dispensacion":
            self.pantalla_dispensacion.pack(fill="both", expand=True)
            self.actualizar_carrito_ui()

    # ==========================================
    # PANTALLA 1: INVENTARIO
    # ==========================================
    def crear_pantalla_inventario(self):
        frame = ctk.CTkFrame(self.contenedor_principal, fg_color="transparent")
        frame.grid_columnconfigure(0, weight=3)
        frame.grid_columnconfigure(1, weight=1)
        frame.grid_rowconfigure(0, weight=1)
        
        # --- IZQUIERDA ---
        frame_izq = ctk.CTkFrame(frame, fg_color=self.color_panel, corner_radius=15)
        frame_izq.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        
        encabezados = ctk.CTkFrame(frame_izq, fg_color="transparent")
        encabezados.pack(fill="x", padx=20, pady=(20, 10))
        ctk.CTkLabel(encabezados, text="ID | MEDICAMENTO", font=("Helvetica", 11, "bold"), text_color=self.color_texto_secundario).pack(side="left")
        ctk.CTkLabel(encabezados, text="PRECIO", font=("Helvetica", 11, "bold"), text_color=self.color_texto_secundario, width=80).pack(side="right", padx=20)
        ctk.CTkLabel(encabezados, text="CANTIDAD", font=("Helvetica", 11, "bold"), text_color=self.color_texto_secundario, width=80).pack(side="right", padx=20)
        
        self.lista_inventario_ui = ctk.CTkScrollableFrame(frame_izq, fg_color="transparent")
        self.lista_inventario_ui.pack(fill="both", expand=True, padx=10, pady=10)
        
        # --- DERECHA ---
        frame_der = ctk.CTkFrame(frame, fg_color=self.color_panel, corner_radius=15)
        frame_der.grid(row=0, column=1, sticky="nsew")
        
        ctk.CTkLabel(frame_der, text="➕ AGREGAR INGRESO", font=("Helvetica", 14, "bold")).pack(anchor="w", padx=20, pady=20)
        
        ctk.CTkLabel(frame_der, text="Nombre del fármaco", text_color=self.color_texto_secundario).pack(anchor="w", padx=20, pady=(10,0))
        self.ent_nombre = ctk.CTkEntry(frame_der, fg_color="#121212", border_width=0, height=35)
        self.ent_nombre.pack(fill="x", padx=20, pady=5)
        
        ctk.CTkLabel(frame_der, text="Cantidad", text_color=self.color_texto_secundario).pack(anchor="w", padx=20, pady=(10,0))
        self.ent_cantidad = ctk.CTkEntry(frame_der, fg_color="#121212", border_width=0, height=35)
        self.ent_cantidad.pack(fill="x", padx=20, pady=5)
        
        ctk.CTkLabel(frame_der, text="Precio", text_color=self.color_texto_secundario).pack(anchor="w", padx=20, pady=(10,0))
        self.ent_precio = ctk.CTkEntry(frame_der, fg_color="#121212", border_width=0, height=35, placeholder_text="S/")
        self.ent_precio.pack(fill="x", padx=20, pady=5)
        
        ctk.CTkButton(frame_der, text="💾 AGREGAR", fg_color=self.color_acento_verde, hover_color="#059669", height=40, font=("Helvetica", 12, "bold"), command=self.guardar_producto_bd).pack(fill="x", padx=20, pady=30)
        
        return frame

    def guardar_producto_bd(self):
        nom, cant, prec = self.ent_nombre.get().strip(), self.ent_cantidad.get().strip(), self.ent_precio.get().strip()
        if not (nom and cant and prec): return messagebox.showwarning("Error", "Campos vacíos")
            
        conexion = sqlite3.connect(DB_NAME)
        cursor = conexion.cursor()
        cursor.execute("INSERT INTO productos (nombre, stock_actual, precio) VALUES (?, ?, ?)", (nom, int(cant), float(prec)))
        conexion.commit()
        conexion.close()
        
        self.ent_nombre.delete(0, 'end'); self.ent_cantidad.delete(0, 'end'); self.ent_precio.delete(0, 'end')
        self.actualizar_lista_inventario()

    def actualizar_lista_inventario(self):
        for widget in self.lista_inventario_ui.winfo_children(): widget.destroy()
            
        conexion = sqlite3.connect(DB_NAME)
        cursor = conexion.cursor()
        cursor.execute("SELECT id, nombre, stock_actual, precio, stock_minimo FROM productos")
        
        for p in cursor.fetchall():
            es_alerta = p[2] <= p[4]
            item = ctk.CTkFrame(self.lista_inventario_ui, fg_color="transparent")
            item.pack(fill="x", pady=5)
            ctk.CTkFrame(item, height=1, fg_color="#2A2A2A").pack(fill="x", side="top", pady=(0, 5))
            
            titulo = f"[{p[0]}] {p[1]} {'⚠️' if es_alerta else ''}"
            ctk.CTkLabel(item, text=titulo, font=("Helvetica", 14, "bold"), text_color=self.color_alerta if es_alerta else "white").pack(side="left", padx=10)
            ctk.CTkLabel(item, text=f"S/ {p[3]:.2f}", font=("Helvetica", 14)).pack(side="right", padx=(20, 10))
            ctk.CTkLabel(item, text=str(p[2]), font=("Helvetica", 14, "bold"), text_color=self.color_alerta if es_alerta else "white", width=50).pack(side="right", padx=20)
        conexion.close()

    # ==========================================
    # PANTALLA 2: DISPENSACIÓN (VENTAS)
    # ==========================================
    def crear_pantalla_dispensacion(self):
        frame = ctk.CTkFrame(self.contenedor_principal, fg_color="transparent")
        frame.grid_columnconfigure(0, weight=3)
        frame.grid_columnconfigure(1, weight=1)
        frame.grid_rowconfigure(0, weight=1)
        
        # --- IZQUIERDA ---
        frame_izq = ctk.CTkFrame(frame, fg_color=self.color_panel, corner_radius=15)
        frame_izq.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        
        frame_busqueda = ctk.CTkFrame(frame_izq, fg_color="transparent")
        frame_busqueda.pack(fill="x", padx=20, pady=20)
        
        self.ent_buscador_venta = ctk.CTkEntry(frame_busqueda, placeholder_text="🔍 Ingrese ID del medicamento + Enter", height=40, fg_color="#121212", border_width=0, corner_radius=8)
        self.ent_buscador_venta.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self.ent_buscador_venta.bind("<Return>", self.agregar_al_carrito) # Detecta cuando presionas Enter
        
        encabezados = ctk.CTkFrame(frame_izq, fg_color="transparent")
        encabezados.pack(fill="x", padx=20, pady=(0, 10))
        ctk.CTkLabel(encabezados, text="MEDICAMENTO", font=("Helvetica", 11, "bold"), text_color=self.color_texto_secundario).pack(side="left")
        ctk.CTkLabel(encabezados, text="PRECIO", font=("Helvetica", 11, "bold"), text_color=self.color_texto_secundario, width=80).pack(side="right")
        ctk.CTkLabel(encabezados, text="CANTIDAD", font=("Helvetica", 11, "bold"), text_color=self.color_texto_secundario, width=120).pack(side="right", padx=20)
        
        self.lista_carrito_ui = ctk.CTkScrollableFrame(frame_izq, fg_color="transparent")
        self.lista_carrito_ui.pack(fill="both", expand=True, padx=10, pady=10)
        
        frame_total = ctk.CTkFrame(frame_izq, fg_color="transparent")
        frame_total.pack(fill="x", side="bottom", padx=20, pady=20)
        ctk.CTkLabel(frame_total, text="TOTAL A PAGAR", font=("Helvetica", 10), text_color=self.color_texto_secundario).pack(anchor="e")
        self.lbl_total = ctk.CTkLabel(frame_total, text="Monto total S/ 0.00", font=("Helvetica", 24, "bold"), text_color=self.color_acento_verde)
        self.lbl_total.pack(anchor="e")

        # --- DERECHA ---
        frame_der = ctk.CTkFrame(frame, fg_color=self.color_panel, corner_radius=15)
        frame_der.grid(row=0, column=1, sticky="nsew")
        
        ctk.CTkLabel(frame_der, text="Método de Registro", font=("Helvetica", 14, "bold")).pack(anchor="w", padx=20, pady=20)
        
        ctk.CTkButton(frame_der, text="💵 EFECTIVO ✔", fg_color="#121212", border_width=1, border_color=self.color_acento_verde, hover_color="#2A2A2A", height=45, anchor="w", corner_radius=8).pack(fill="x", padx=20, pady=5)
        
        ctk.CTkLabel(frame_der, text="Efectivo Recibido", font=("Helvetica", 11), text_color=self.color_texto_secundario).pack(anchor="w", padx=20, pady=(20,0))
        self.ent_efectivo = ctk.CTkEntry(frame_der, fg_color="#121212", border_width=0, height=40, placeholder_text="S/ 0.00")
        self.ent_efectivo.pack(fill="x", padx=20, pady=5)
        self.ent_efectivo.bind("<KeyRelease>", self.calcular_vuelto) # Calcula en tiempo real al teclear
        
        ctk.CTkLabel(frame_der, text="Vuelto", font=("Helvetica", 11), text_color=self.color_texto_secundario).pack(anchor="w", padx=20, pady=(10,0))
        self.lbl_vuelto = ctk.CTkLabel(frame_der, text="S/ 0.00", font=("Helvetica", 16, "bold"), text_color=self.color_acento_verde, fg_color="#121212", height=40, corner_radius=8, anchor="w")
        self.lbl_vuelto.pack(fill="x", padx=20, pady=5)
        
        frame_acciones = ctk.CTkFrame(frame_der, fg_color="transparent")
        frame_acciones.pack(fill="x", side="bottom", padx=20, pady=20)
        ctk.CTkButton(frame_acciones, text="CANCELAR", fg_color="#121212", border_width=1, border_color="#3F3F46", hover_color="#2A2A2A", height=45, width=90, command=self.limpiar_carrito).pack(side="left")
        ctk.CTkButton(frame_acciones, text="COMPLETAR\nREGISTRO →", fg_color=self.color_acento_verde, hover_color="#059669", height=45, font=("Helvetica", 12, "bold"), command=self.completar_registro).pack(side="right", fill="x", expand=True, padx=(10,0))
        
        return frame

    # ==========================================
    # LÓGICA DE VENTAS Y CARRITO
    # ==========================================
    def agregar_al_carrito(self, event=None):
        id_ingresado = self.ent_buscador_venta.get().strip()
        if not id_ingresado.isdigit(): return
        
        id_prod = int(id_ingresado)
        conexion = sqlite3.connect(DB_NAME)
        cursor = conexion.cursor()
        cursor.execute("SELECT nombre, precio, stock_actual FROM productos WHERE id = ?", (id_prod,))
        prod = cursor.fetchone()
        conexion.close()
        
        self.ent_buscador_venta.delete(0, 'end')
        
        if not prod: return messagebox.showwarning("Error", "ID no existe")
        if prod[2] <= 0: return messagebox.showwarning("Agotado", "No hay stock")

        if id_prod in self.carrito:
            if self.carrito[id_prod]['cantidad'] < prod[2]:
                self.carrito[id_prod]['cantidad'] += 1
            else:
                messagebox.showwarning("Límite", "No hay más stock disponible")
        else:
            self.carrito[id_prod] = {'nombre': prod[0], 'precio': prod[1], 'cantidad': 1, 'stock_max': prod[2]}
            
        self.actualizar_carrito_ui()

    def modificar_cantidad(self, id_prod, operacion):
        if operacion == "suma":
            if self.carrito[id_prod]['cantidad'] < self.carrito[id_prod]['stock_max']:
                self.carrito[id_prod]['cantidad'] += 1
        elif operacion == "resta":
            self.carrito[id_prod]['cantidad'] -= 1
            if self.carrito[id_prod]['cantidad'] <= 0:
                del self.carrito[id_prod]
                
        self.actualizar_carrito_ui()

    def actualizar_carrito_ui(self):
        for widget in self.lista_carrito_ui.winfo_children(): widget.destroy()
        
        self.total_actual = 0.0
        
        for id_prod, datos in self.carrito.items():
            subtotal = datos['cantidad'] * datos['precio']
            self.total_actual += subtotal
            
            item = ctk.CTkFrame(self.lista_carrito_ui, fg_color="transparent")
            item.pack(fill="x", pady=5)
            ctk.CTkFrame(item, height=1, fg_color="#2A2A2A").pack(fill="x", side="top", pady=(0, 10))
            
            info_frame = ctk.CTkFrame(item, fg_color="transparent")
            info_frame.pack(fill="x")
            
            ctk.CTkLabel(info_frame, text=datos['nombre'], font=("Helvetica", 14, "bold")).pack(side="left", padx=10)
            ctk.CTkLabel(info_frame, text=f"S/ {subtotal:.2f}", font=("Helvetica", 14)).pack(side="right", padx=(20, 10))
            
            # Botones + y -
            cant_frame = ctk.CTkFrame(info_frame, fg_color="#121212", corner_radius=5)
            cant_frame.pack(side="right", padx=20)
            
            btn_resta = ctk.CTkButton(cant_frame, text="-", width=25, height=25, fg_color="transparent", command=lambda i=id_prod: self.modificar_cantidad(i, "resta"))
            btn_resta.pack(side="left", padx=2)
            
            ctk.CTkLabel(cant_frame, text=str(datos['cantidad']), font=("Helvetica", 14, "bold"), width=30).pack(side="left")
            
            btn_suma = ctk.CTkButton(cant_frame, text="+", width=25, height=25, fg_color="transparent", command=lambda i=id_prod: self.modificar_cantidad(i, "suma"))
            btn_suma.pack(side="left", padx=2)

        self.lbl_total.configure(text=f"Monto total S/ {self.total_actual:.2f}")
        self.calcular_vuelto()

    def calcular_vuelto(self, event=None):
        try:
            efectivo = float(self.ent_efectivo.get())
            vuelto = efectivo - self.total_actual
            if vuelto >= 0:
                self.lbl_vuelto.configure(text=f"S/ {vuelto:.2f}", text_color=self.color_acento_verde)
            else:
                self.lbl_vuelto.configure(text="Falta dinero", text_color=self.color_alerta)
        except ValueError:
            self.lbl_vuelto.configure(text="S/ 0.00", text_color=self.color_acento_verde)

    def limpiar_carrito(self):
        self.carrito = {}
        self.ent_efectivo.delete(0, 'end')
        self.actualizar_carrito_ui()

    def completar_registro(self):
        if not self.carrito: return
        
        conexion = sqlite3.connect(DB_NAME)
        cursor = conexion.cursor()
        alertas = []
        
        # Procesar descuento de base de datos
        for id_prod, datos in self.carrito.items():
            cant = datos['cantidad']
            cursor.execute("UPDATE productos SET stock_actual = stock_actual - ?, ventas_hoy = ventas_hoy + ? WHERE id = ?", (cant, cant, id_prod))
            
            # Verificar si bajó del mínimo
            cursor.execute("SELECT nombre, stock_actual, stock_minimo FROM productos WHERE id = ?", (id_prod,))
            p = cursor.fetchone()
            if p[1] <= p[2]: alertas.append(p)

        conexion.commit()
        conexion.close()
        self.limpiar_carrito()
        
        # Lógica de WhatsApp
        if alertas:
            prod_critico = alertas[0]
            if messagebox.askyesno("Stock Crítico", f"'{prod_critico[0]}' está en escasez ({prod_critico[1]} uds).\n\n¿Enviar alerta automática a WhatsApp?"):
                # REEMPLAZA EL NÚMERO AQUÍ POR EL TUYO PARA PROBAR
                num = "+51913704428" 
                msj = urllib.parse.quote(f"🚨 ALERTA QALINODE: Urgente reabastecer {prod_critico[0]}. Stock actual: {prod_critico[1]}.")
                webbrowser.open(f"https://wa.me/{num}?text={msj}")
        else:
            messagebox.showinfo("Éxito", "Dispensación completada y registrada.")

if __name__ == "__main__":
    inicializar_bd()
    app = ChasquiLogApp()
    app.mainloop()