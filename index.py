import customtkinter as ctk
import sqlite3
import threading
import json
import os
import socket
import urllib.request
import urllib.error
import urllib.parse
import webbrowser
from datetime import datetime
from tkinter import messagebox

# Pillow se usa para dibujar el comprobante como imagen PNG (funciona 100% offline)
try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_DISPONIBLE = True
except ImportError:
    PIL_DISPONIBLE = False

# Configuración global
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("green")
DB_NAME = "qalinode_pos.db"
CARPETA_COMPROBANTES = "comprobantes"

# ==========================================
# CONFIGURACIÓN DE GEMMA LOCAL (OFFLINE, vía Ollama)
# ==========================================
# 1) Instalar Ollama (https://ollama.com) — corre en localhost, sin internet.
# 2) Descargar el modelo UNA VEZ con internet:  ollama pull gemma3:4b
# 3) Dejarlo corriendo:  ollama serve
OLLAMA_URL = "http://localhost:11434/api/generate"
GEMMA_MODEL = "gemma3:4b"
GEMMA_TIMEOUT = 60

# ==========================================
# CONFIGURACIÓN DE WHATSAPP (GRATUITO — CallMeBot)
# ==========================================
# CallMeBot es gratis y permite envío AUTOMÁTICO (sin abrir navegador ni dar clic).
# Configuración única, desde el celular DEL DUEÑO (2 minutos, requiere internet una vez):
#   1) Agregar a contactos el número:  +34 621 331 709
#   2) Enviarle por WhatsApp el mensaje exacto:  I allow callmebot to send me messages
#   3) El bot responde con una API key -> pegarla abajo en CALLMEBOT_APIKEY
# Limitación real: CallMeBot SOLO envía a números que hicieron esa autorización.
# Por eso sirve para la alerta al dueño, pero NO para clientes cualquiera.
NUM_DUENO = "+51913704428"          # número del responsable de reabastecimiento
CALLMEBOT_APIKEY = ""               # <-- pegar aquí la apikey que responde el bot

# Datos de la posta que se imprimen en el comprobante
DATOS_POSTA = {
    "nombre": "POSTA MEDICA RURAL - CHASQUI-LOG",
    "ruc": "20123456789",
    "direccion": "Av. Principal S/N, Comunidad Rural",
    "id_posta": "045-SUR",
    "telefono": "+51 913 704 428",
}


# ==========================================
# UTILIDADES: GEMMA Y CONECTIVIDAD
# ==========================================
def llamar_gemma(prompt: str):
    """Llama al modelo Gemma corriendo localmente en Ollama. Devuelve texto o None."""
    try:
        payload = json.dumps({
            "model": GEMMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.3}
        }).encode("utf-8")
        req = urllib.request.Request(OLLAMA_URL, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=GEMMA_TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            texto = body.get("response", "").strip()
            return texto if texto else None
    except Exception:
        return None


def hay_internet(timeout=2.5):
    """Detecta conexión real abriendo un socket a DNS públicos."""
    for host, port in (("8.8.8.8", 53), ("1.1.1.1", 53)):
        try:
            s = socket.create_connection((host, port), timeout=timeout)
            s.close()
            return True
        except OSError:
            continue
    return False


def obtener_ubicacion_ip():
    """Obtiene ubicación aproximada por IP usando ipinfo.io (más estable).
    >>> CAMBIO: Proveedor actualizado y User-Agent de navegador simulado <<<"""
    try:
        # Usamos un User-Agent de navegador real para evitar el bloqueo del servidor
        req = urllib.request.Request(
            "https://ipinfo.io/json",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            
            # ipinfo devuelve la latitud y longitud juntas en un solo string: "lat,lon"
            if "loc" in data:
                lat, lon = map(float, data["loc"].split(","))
            else:
                lat, lon = 0.0, 0.0
                
            return {
                "lat": lat,
                "lon": lon,
                "ciudad": data.get("city", "Desconocida"),
                "region": data.get("region", ""),
                "pais": data.get("country", "Perú"),
            }
    except Exception as e:
        # Imprime el error en la consola oculta para facilitar la depuración si vuelve a fallar
        print(f"[Error de Geolocalización]: {e}")
        return None


# Códigos WMO → emoji + descripción legible
_WMO = {
    0: ("☀️", "Despejado"),     1: ("🌤", "Mayormente despejado"),
    2: ("⛅", "Parcialmente nublado"), 3: ("☁️", "Nublado"),
    45: ("🌫", "Niebla"),        48: ("🌫", "Niebla con escarcha"),
    51: ("🌦", "Llovizna"),      53: ("🌦", "Llovizna moderada"),   55: ("🌧", "Llovizna densa"),
    61: ("🌧", "Lluvia leve"),   63: ("🌧", "Lluvia moderada"),     65: ("🌧", "Lluvia fuerte"),
    71: ("🌨", "Nevada leve"),   73: ("🌨", "Nevada moderada"),     75: ("❄️", "Nevada fuerte"),
    77: ("🌨", "Granizo"),
    80: ("🌦", "Chubascos"),     81: ("🌧", "Chubascos mod."),      82: ("⛈", "Chubascos fuertes"),
    85: ("🌨", "Chubascos nieve"), 86: ("❄️", "Chubascos nieve fuertes"),
    95: ("⛈", "Tormenta"),      96: ("⛈", "Tormenta c/granizo"), 99: ("⛈", "Tormenta fuerte"),
}


def obtener_pronostico_clima(lat, lon):
    """Obtiene pronóstico de 7 días desde Open-Meteo (gratis, sin API key).
    Retorna lista de dicts: {fecha, emoji, desc, lluvia_mm, viento_kmh, horas_lluvia, malo}"""
    try:
        params = urllib.parse.urlencode({
            "latitude": lat, "longitude": lon,
            "daily": "weather_code,precipitation_sum,wind_speed_10m_max,precipitation_hours",
            "timezone": "auto", "forecast_days": 7
        })
        url = f"https://api.open-meteo.com/v1/forecast?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": "ChasquiLog/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            d = json.loads(resp.read().decode("utf-8"))["daily"]
        dias = []
        nombres_dia = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
        for i in range(len(d["time"])):
            fecha_str  = d["time"][i]                   # "2025-08-02"
            fecha_obj  = datetime.strptime(fecha_str, "%Y-%m-%d")
            wmo        = int(d["weather_code"][i] or 0)
            lluvia     = float(d["precipitation_sum"][i] or 0)
            viento     = float(d["wind_speed_10m_max"][i] or 0)
            h_lluvia   = float(d["precipitation_hours"][i] or 0)
            emoji, desc = _WMO.get(wmo, ("🌡", "Variable"))
            malo = lluvia > 5 or viento > 50 or wmo in (65, 75, 82, 86, 95, 96, 99)
            dias.append({
                "fecha":      fecha_str,
                "nombre_dia": nombres_dia[fecha_obj.weekday()],
                "dia_num":    fecha_obj.day,
                "emoji":      emoji,
                "desc":       desc,
                "lluvia_mm":  round(lluvia, 1),
                "viento_kmh": round(viento, 1),
                "h_lluvia":   round(h_lluvia, 1),
                "malo":       malo,
            })
        return dias
    except Exception:
        return []


def obtener_mercados_cercanos(lat, lon, radio_km=30):
    """Busca farmacias, mercados y tiendas en OpenStreetMap (Overpass API, gratis).
    Amplía a 60 km si no encuentra nada. Retorna lista de dicts {nombre, tipo, dist_km}."""
    def _consulta(lat, lon, radio_m):
        query = (
            f"[out:json][timeout:10];"
            f"("
            f"node[\"amenity\"=\"pharmacy\"](around:{radio_m},{lat},{lon});"
            f"node[\"amenity\"=\"hospital\"](around:{radio_m},{lat},{lon});"
            f"node[\"shop\"=\"supermarket\"](around:{radio_m},{lat},{lon});"
            f"node[\"shop\"=\"general\"](around:{radio_m},{lat},{lon});"
            f"node[\"shop\"=\"medical_supply\"](around:{radio_m},{lat},{lon});"
            f");"
            f"out body;"
        )
        url = "https://overpass-api.de/api/interpreter"
        data = urllib.parse.urlencode({"data": query}).encode("utf-8")
        req  = urllib.request.Request(url, data=data,
                                      headers={"Content-Type": "application/x-www-form-urlencoded",
                                               "User-Agent": "ChasquiLog/1.0"})
        with urllib.request.urlopen(req, timeout=12) as resp:
            return json.loads(resp.read().decode("utf-8"))["elements"]

    import math
    def _dist(lat1, lon1, lat2, lon2):
        R = 6371
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1))*math.cos(math.radians(lat2))*math.sin(dlon/2)**2
        return round(R * 2 * math.asin(math.sqrt(a)), 1)

    iconos_tipo = {
        "pharmacy": ("💊", "Farmacia"), "hospital": ("🏥", "Hospital"),
        "supermarket": ("🛒", "Supermercado"), "general": ("🏪", "Tienda"),
        "medical_supply": ("🩺", "Insumos médicos"),
    }
    try:
        elementos = _consulta(lat, lon, radio_km * 1000)
        if not elementos:
            elementos = _consulta(lat, lon, 60 * 1000)   # ampliar a 60 km
        resultados = []
        vistos = set()
        for e in elementos:
            tags   = e.get("tags", {})
            nombre = tags.get("name") or tags.get("brand") or "Sin nombre"
            if nombre in vistos:
                continue
            vistos.add(nombre)
            tipo_osm = tags.get("amenity") or tags.get("shop", "")
            icono, tipo_label = iconos_tipo.get(tipo_osm, ("🏪", "Establecimiento"))
            dist = _dist(lat, lon, e.get("lat", lat), e.get("lon", lon))
            resultados.append({"nombre": nombre, "tipo": tipo_label, "icono": icono, "dist_km": dist})
        resultados.sort(key=lambda x: x["dist_km"])
        return resultados[:8]   # máx 8 lugares
    except Exception:
        return []


def enviar_whatsapp_callmebot(numero: str, mensaje: str):
    """Envío AUTOMÁTICO por WhatsApp (gratis) usando CallMeBot.
    Devuelve (exito, detalle). Requiere internet y apikey configurada."""
    if not CALLMEBOT_APIKEY:
        return False, "CALLMEBOT_APIKEY no configurada"
    numero_limpio = numero.replace(" ", "").replace("-", "")
    url = (
        "https://api.callmebot.com/whatsapp.php"
        f"?phone={urllib.parse.quote(numero_limpio)}"
        f"&text={urllib.parse.quote(mensaje)}"
        f"&apikey={urllib.parse.quote(CALLMEBOT_APIKEY)}"
    )
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            resp.read()
        return True, "Enviado por CallMeBot"
    except Exception as e:
        return False, f"Error CallMeBot: {e}"


def enviar_imagen_whatsapp(numero: str, ruta_imagen: str, caption: str):
    """Envía la IMAGEN del comprobante con pywhatkit (gratis, vía WhatsApp Web).
    Requiere internet, navegador y sesión activa de WhatsApp Web.
    Instalación:  pip install pywhatkit"""
    try:
        import pywhatkit  # importación diferida: solo se carga si se va a usar
        numero_limpio = numero.replace(" ", "").replace("-", "")
        pywhatkit.sendwhats_image(
            receiver=numero_limpio,
            img_path=os.path.abspath(ruta_imagen),
            caption=caption,
            wait_time=25,
            tab_close=True
        )
        return True, "Imagen enviada por WhatsApp Web"
    except ImportError:
        return False, "pywhatkit no instalado (pip install pywhatkit)"
    except Exception as e:
        return False, f"Error al enviar imagen: {e}"


def abrir_link_whatsapp(numero: str, mensaje: str):
    """Respaldo universal gratuito: abre wa.me con el mensaje precargado.
    Funciona con cualquier número, pero el usuario debe dar clic en Enviar."""
    numero_limpio = numero.replace(" ", "").replace("-", "").replace("+", "")
    webbrowser.open(f"https://wa.me/{numero_limpio}?text={urllib.parse.quote(mensaje)}")


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

    cursor.execute('''CREATE TABLE IF NOT EXISTS ventas_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            producto_id INTEGER,
            nombre TEXT NOT NULL,
            cantidad INTEGER NOT NULL,
            precio_unit REAL NOT NULL,
            subtotal REAL NOT NULL,
            metodo_pago TEXT NOT NULL,
            fecha_hora TEXT NOT NULL)''')

    # Comprobantes emitidos: correlativo local, funciona sin internet
    cursor.execute('''CREATE TABLE IF NOT EXISTS comprobantes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            serie TEXT NOT NULL,
            numero INTEGER NOT NULL,
            cliente_num TEXT,
            total REAL NOT NULL,
            metodo_pago TEXT NOT NULL,
            ruta_txt TEXT,
            ruta_img TEXT,
            fecha_hora TEXT NOT NULL)''')

    # Cola de envíos: todo lo que necesita internet espera aquí hasta que haya señal
    cursor.execute('''CREATE TABLE IF NOT EXISTS cola_envios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tipo TEXT NOT NULL,
            destino TEXT NOT NULL,
            mensaje TEXT NOT NULL,
            adjunto TEXT,
            estado TEXT DEFAULT 'pendiente',
            intentos INTEGER DEFAULT 0,
            fecha_creado TEXT NOT NULL,
            fecha_enviado TEXT)''')

    cursor.execute("SELECT COUNT(*) FROM productos")
    if cursor.fetchone()[0] == 0:
        demo_data = [
            ("Amoxicilina 500mg", 142, 12.50, 20, 0),
            ("Paracetamol 1g", 8, 2.00, 15, 0),
            ("Ibuprofeno 400mg", 85, 18.00, 25, 0)
        ]
        cursor.executemany("INSERT INTO productos (nombre, stock_actual, precio, stock_minimo, ventas_hoy) VALUES (?, ?, ?, ?, ?)", demo_data)

    cursor.execute("UPDATE productos SET stock_actual = 0 WHERE stock_actual < 0")
    conexion.commit()
    conexion.close()

    os.makedirs(CARPETA_COMPROBANTES, exist_ok=True)


# ==========================================
# GENERACIÓN DEL COMPROBANTE
# ==========================================
def siguiente_correlativo():
    """Correlativo local: B001-00000001, B001-00000002, ..."""
    conexion = sqlite3.connect(DB_NAME)
    cursor = conexion.cursor()
    cursor.execute("SELECT COALESCE(MAX(numero), 0) FROM comprobantes WHERE serie = 'B001'")
    numero = cursor.fetchone()[0] + 1
    conexion.close()
    return "B001", numero


def plantilla_comprobante(venta):
    """Comprobante determinístico (respaldo). Los montos SIEMPRE se calculan aquí,
    nunca los inventa el modelo."""
    ancho = 44
    lineas = [
        DATOS_POSTA["nombre"].center(ancho),
        f"RUC: {DATOS_POSTA['ruc']}".center(ancho),
        DATOS_POSTA["direccion"].center(ancho),
        f"Posta {DATOS_POSTA['id_posta']} | Tel. {DATOS_POSTA['telefono']}".center(ancho),
        "=" * ancho,
        "BOLETA DE VENTA ELECTRONICA".center(ancho),
        f"{venta['serie']}-{venta['numero']:08d}".center(ancho),
        "=" * ancho,
        f"Fecha  : {venta['fecha_legible']}",
        f"Cliente: {venta['cliente_num'] or 'Cliente varios'}",
        f"Pago   : {venta['metodo_pago']}",
        "-" * ancho,
        f"{'CANT':>4} {'DESCRIPCION':<24}{'IMPORTE':>13}",
        "-" * ancho,
    ]
    for it in venta["items"]:
        nombre = it["nombre"][:24]
        importe = "S/ " + format(it["subtotal"], ".2f")
        lineas.append(f"{it['cantidad']:>4} {nombre:<24}{importe:>13}")
        lineas.append(f"     P.U. S/ {it['precio']:.2f}")
    lineas += [
        "-" * ancho,
        f"{'OP. GRAVADA:':>30} {'S/ ' + format(venta['gravada'], '.2f'):>12}",
        f"{'IGV (18%):':>30} {'S/ ' + format(venta['igv'], '.2f'):>12}",
        f"{'TOTAL:':>30} {'S/ ' + format(venta['total'], '.2f'):>12}",
        "=" * ancho,
        "Gracias por su confianza.".center(ancho),
        "Documento emitido en modo offline".center(ancho),
        "Representacion impresa - Chasqui-Log".center(ancho),
    ]
    return "\n".join(lineas)


def generar_texto_comprobante(venta):
    """Gemma redacta el comprobante con formato realista de boleta peruana.
    VALIDACION: si el texto devuelto no contiene el total exacto calculado por la app,
    se descarta y se usa la plantilla determinística. Así el modelo nunca altera montos."""
    detalle = "\n".join(
        f"- {it['cantidad']} x {it['nombre']} | P.U. S/ {it['precio']:.2f} | Importe S/ {it['subtotal']:.2f}"
        for it in venta["items"]
    )
    prompt = (
        "Eres el sistema de facturación de una posta médica rural en Perú. Genera una BOLETA DE VENTA "
        "ELECTRÓNICA en texto plano, con formato de ticket de impresora térmica de 44 caracteres de ancho.\n\n"
        "REGLAS ESTRICTAS:\n"
        "- Usa EXCLUSIVAMENTE los datos que te doy. Está PROHIBIDO inventar, redondear o modificar cifras.\n"
        "- Copia los montos exactamente como aparecen, con dos decimales y el prefijo S/.\n"
        "- No agregues productos, descuentos ni impuestos que no estén en la lista.\n"
        "- Devuelve SOLO el ticket, sin explicaciones, sin comentarios y sin bloques de código.\n"
        "- Estructura: encabezado del establecimiento, tipo y número de documento, fecha, cliente, método "
        "de pago, detalle de productos, líneas de op. gravada / IGV / TOTAL y un pie de agradecimiento.\n"
        "- Usa líneas de '=' y '-' como separadores y centra el encabezado.\n\n"
        "DATOS:\n"
        f"Establecimiento: {DATOS_POSTA['nombre']}\n"
        f"RUC: {DATOS_POSTA['ruc']}\n"
        f"Dirección: {DATOS_POSTA['direccion']}\n"
        f"Código de posta: {DATOS_POSTA['id_posta']}\n"
        f"Documento: BOLETA DE VENTA ELECTRÓNICA {venta['serie']}-{venta['numero']:08d}\n"
        f"Fecha y hora: {venta['fecha_legible']}\n"
        f"Cliente (celular): {venta['cliente_num'] or 'Cliente varios'}\n"
        f"Método de pago: {venta['metodo_pago']}\n"
        f"Productos:\n{detalle}\n"
        f"Operación gravada: S/ {venta['gravada']:.2f}\n"
        f"IGV (18%): S/ {venta['igv']:.2f}\n"
        f"TOTAL A PAGAR: S/ {venta['total']:.2f}\n"
    )

    texto = llamar_gemma(prompt)
    if texto:
        texto = texto.replace("```", "").strip()
        total_str = f"{venta['total']:.2f}"
        # Control de calidad: el total exacto debe aparecer tal cual en el texto generado
        if total_str in texto and len(texto) > 120:
            return texto, "gemma"
    return plantilla_comprobante(venta), "plantilla"


def _cargar_fuente_mono(tam):
    """Busca una fuente monoespaciada en el sistema (Windows/Linux/macOS)."""
    rutas = [
        "C:/Windows/Fonts/consola.ttf",
        "C:/Windows/Fonts/cour.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/System/Library/Fonts/Menlo.ttc",
    ]
    for ruta in rutas:
        if os.path.exists(ruta):
            try:
                return ImageFont.truetype(ruta, tam)
            except Exception:
                continue
    return ImageFont.load_default()


def generar_imagen_comprobante(texto, ruta_png):
    """Dibuja el ticket como PNG blanco/negro, estilo impresora térmica. Todo offline."""
    if not PIL_DISPONIBLE:
        return None
    fuente = _cargar_fuente_mono(16)
    lineas = texto.split("\n")
    alto_linea, margen, ancho = 22, 25, 620
    alto = margen * 2 + alto_linea * len(lineas)

    img = Image.new("RGB", (ancho, alto), "white")
    draw = ImageDraw.Draw(img)
    y = margen
    for linea in lineas:
        draw.text((margen, y), linea, font=fuente, fill="black")
        y += alto_linea
    img.save(ruta_png)
    return ruta_png


# ==========================================
# COLA DE ENVIOS (para operar sin internet)
# ==========================================
def encolar_envio(tipo, destino, mensaje, adjunto=None):
    conexion = sqlite3.connect(DB_NAME)
    cursor = conexion.cursor()
    cursor.execute(
        "INSERT INTO cola_envios (tipo, destino, mensaje, adjunto, fecha_creado) VALUES (?, ?, ?, ?, ?)",
        (tipo, destino, mensaje, adjunto, datetime.now().isoformat(timespec="seconds")))
    conexion.commit()
    conexion.close()


def contar_pendientes():
    conexion = sqlite3.connect(DB_NAME)
    cursor = conexion.cursor()
    cursor.execute("SELECT COUNT(*) FROM cola_envios WHERE estado = 'pendiente'")
    n = cursor.fetchone()[0]
    conexion.close()
    return n


def procesar_cola():
    """Intenta enviar todo lo pendiente. Se llama sola al detectar internet."""
    if not hay_internet():
        return 0, 0

    conexion = sqlite3.connect(DB_NAME)
    cursor = conexion.cursor()
    cursor.execute("SELECT id, tipo, destino, mensaje, adjunto FROM cola_envios WHERE estado = 'pendiente' ORDER BY id")
    pendientes = cursor.fetchall()
    conexion.close()

    enviados, fallidos = 0, 0
    for id_envio, tipo, destino, mensaje, adjunto in pendientes:
        exito = False
        if tipo == "alerta_stock":
            exito, _ = enviar_whatsapp_callmebot(destino, mensaje)
        elif tipo == "comprobante":
            if adjunto and os.path.exists(adjunto):
                exito, _ = enviar_imagen_whatsapp(destino, adjunto, "Su comprobante de pago")
            if not exito:
                exito, _ = enviar_whatsapp_callmebot(destino, mensaje)

        conexion = sqlite3.connect(DB_NAME)
        cursor = conexion.cursor()
        if exito:
            cursor.execute("UPDATE cola_envios SET estado = 'enviado', fecha_enviado = ?, intentos = intentos + 1 WHERE id = ?",
                           (datetime.now().isoformat(timespec="seconds"), id_envio))
            enviados += 1
        else:
            cursor.execute("UPDATE cola_envios SET intentos = intentos + 1 WHERE id = ?", (id_envio,))
            fallidos += 1
        conexion.commit()
        conexion.close()

    return enviados, fallidos


# ==========================================
# VENTANA MODAL: COMPROBANTE DE PAGO
# ==========================================
class VentanaComprobante(ctk.CTkToplevel):
    def __init__(self, master, venta):
        super().__init__(master)
        self.app = master
        self.venta = venta

        self.title("Comprobante de pago")
        self.geometry("480x430")
        self.configure(fg_color="#121212")
        self.resizable(False, False)
        self.transient(master)
        self.after(120, self.grab_set)  # bloquea la ventana principal hasta cerrar

        self.contenedor = ctk.CTkFrame(self, fg_color="#1E1E1E", corner_radius=15)
        self.contenedor.pack(fill="both", expand=True, padx=20, pady=20)

        self.paso_pregunta()

    # ---------- Paso 1: ¿desea comprobante? ----------
    def paso_pregunta(self):
        self._limpiar()
        ctk.CTkLabel(self.contenedor, text="✅ Venta registrada", font=("Helvetica", 16, "bold"),
                     text_color=self.app.color_acento_verde).pack(pady=(30, 5))
        ctk.CTkLabel(self.contenedor, text=f"Total: S/ {self.venta['total']:.2f}  ·  {self.venta['metodo_pago']}",
                     font=("Helvetica", 13), text_color=self.app.color_texto_secundario).pack(pady=(0, 25))

        ctk.CTkLabel(self.contenedor, text="¿Desea comprobante de pago?",
                     font=("Helvetica", 18, "bold"), text_color="white").pack(pady=(0, 30))

        fila = ctk.CTkFrame(self.contenedor, fg_color="transparent")
        fila.pack(pady=10)
        ctk.CTkButton(fila, text="SÍ", width=140, height=50, font=("Helvetica", 14, "bold"),
                      fg_color=self.app.color_acento_verde, hover_color="#059669",
                      command=self.paso_numero).pack(side="left", padx=10)
        ctk.CTkButton(fila, text="NO", width=140, height=50, font=("Helvetica", 14, "bold"),
                      fg_color="#121212", border_width=1, border_color="#3F3F46",
                      hover_color="#2A2A2A", command=self.destroy).pack(side="left", padx=10)

    # ---------- Paso 2: número del cliente ----------
    def paso_numero(self):
        self._limpiar()
        ctk.CTkLabel(self.contenedor, text="📱 Número del cliente", font=("Helvetica", 16, "bold"),
                     text_color="white").pack(pady=(30, 5))
        ctk.CTkLabel(self.contenedor, text="Incluye el código de país (ej. +51987654321)",
                     font=("Helvetica", 12), text_color=self.app.color_texto_secundario).pack(pady=(0, 20))

        self.ent_numero = ctk.CTkEntry(self.contenedor, height=45, font=("Helvetica", 15),
                                       fg_color="#121212", border_width=0, justify="center")
        self.ent_numero.pack(fill="x", padx=40, pady=5)
        self.ent_numero.insert(0, "+51")
        self.ent_numero.focus()
        self.ent_numero.bind("<Return>", lambda e: self.generar())

        self.lbl_error = ctk.CTkLabel(self.contenedor, text="", font=("Helvetica", 12),
                                      text_color=self.app.color_alerta)
        self.lbl_error.pack(pady=5)

        ctk.CTkButton(self.contenedor, text="GENERAR COMPROBANTE CON GEMMA", height=45,
                      font=("Helvetica", 13, "bold"), fg_color=self.app.color_acento_verde,
                      hover_color="#059669", command=self.generar).pack(fill="x", padx=40, pady=(15, 5))
        ctk.CTkButton(self.contenedor, text="← Volver", fg_color="transparent",
                      text_color=self.app.color_texto_secundario, hover_color="#2A2A2A",
                      command=self.paso_pregunta).pack(pady=5)

    # ---------- Paso 3: generación + envío ----------
    def generar(self):
        numero = self.ent_numero.get().strip().replace(" ", "")
        solo_digitos = numero.lstrip("+")
        if not solo_digitos.isdigit() or len(solo_digitos) < 9:
            self.lbl_error.configure(text="Número inválido. Usa formato +51987654321")
            return

        self.venta["cliente_num"] = numero
        self._limpiar()
        ctk.CTkLabel(self.contenedor, text="🧠 Generando comprobante con Gemma...",
                     font=("Helvetica", 15, "bold"), text_color=self.app.color_acento_verde).pack(pady=(60, 10))
        ctk.CTkLabel(self.contenedor, text="Procesando en local, sin internet.",
                     font=("Helvetica", 12), text_color=self.app.color_texto_secundario,
                     wraplength=380, justify="center").pack(pady=10)
        threading.Thread(target=self._worker_generar, daemon=True).start()

    def _worker_generar(self):
        venta = self.venta
        serie, numero_doc = siguiente_correlativo()
        venta["serie"], venta["numero"] = serie, numero_doc

        texto, origen = generar_texto_comprobante(venta)

        base = os.path.join(CARPETA_COMPROBANTES, f"{serie}-{numero_doc:08d}")
        ruta_txt = base + ".txt"
        with open(ruta_txt, "w", encoding="utf-8") as f:
            f.write(texto)

        ruta_img = generar_imagen_comprobante(texto, base + ".png")

        conexion = sqlite3.connect(DB_NAME)
        cursor = conexion.cursor()
        cursor.execute(
            "INSERT INTO comprobantes (serie, numero, cliente_num, total, metodo_pago, ruta_txt, ruta_img, fecha_hora) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (serie, numero_doc, venta["cliente_num"], venta["total"], venta["metodo_pago"],
             ruta_txt, ruta_img, datetime.now().isoformat(timespec="seconds")))
        conexion.commit()
        conexion.close()

        # Envío por WhatsApp: requiere internet obligatoriamente
        if hay_internet():
            exito = False
            if ruta_img:
                exito, _ = enviar_imagen_whatsapp(venta["cliente_num"], ruta_img, "Su comprobante de pago")
            if not exito:
                exito, _ = enviar_whatsapp_callmebot(venta["cliente_num"], texto)
            if not exito:
                abrir_link_whatsapp(venta["cliente_num"], texto)
                estado_envio = "Se abrió WhatsApp con el comprobante listo: solo da clic en Enviar."
            else:
                estado_envio = f"Enviado por WhatsApp al {venta['cliente_num']}."
        else:
            encolar_envio("comprobante", venta["cliente_num"], texto, ruta_img)
            estado_envio = ("Sin internet: el comprobante quedó en la cola y se enviará "
                            "automáticamente cuando haya señal.")

        self.after(0, lambda: self._mostrar_resultado(serie, numero_doc, ruta_txt, ruta_img, origen, estado_envio))

    def _mostrar_resultado(self, serie, numero_doc, ruta_txt, ruta_img, origen, estado_envio):
        self._limpiar()
        ctk.CTkLabel(self.contenedor, text="🧾 Comprobante generado", font=("Helvetica", 17, "bold"),
                     text_color=self.app.color_acento_verde).pack(pady=(30, 5))
        ctk.CTkLabel(self.contenedor, text=f"{serie}-{numero_doc:08d}", font=("Helvetica", 14, "bold"),
                     text_color="white").pack(pady=(0, 10))

        etiqueta_origen = "Redactado por Gemma (local)" if origen == "gemma" else "Plantilla local (Gemma no disponible)"
        ctk.CTkLabel(self.contenedor, text=etiqueta_origen, font=("Helvetica", 11),
                     text_color=self.app.color_texto_secundario).pack()

        ctk.CTkLabel(self.contenedor, text=estado_envio, font=("Helvetica", 12),
                     text_color=self.app.color_alerta if "Sin internet" in estado_envio else "white",
                     wraplength=380, justify="center").pack(pady=15, padx=20)

        fila = ctk.CTkFrame(self.contenedor, fg_color="transparent")
        fila.pack(pady=5)
        if ruta_img:
            ctk.CTkButton(fila, text="👁 Ver imagen", width=130, fg_color="#121212", border_width=1,
                          border_color=self.app.color_acento_verde, text_color=self.app.color_acento_verde,
                          hover_color="#2A2A2A",
                          command=lambda: webbrowser.open("file://" + os.path.abspath(ruta_img))).pack(side="left", padx=5)
        ctk.CTkButton(fila, text="📄 Ver TXT", width=130, fg_color="#121212", border_width=1,
                      border_color="#3F3F46", hover_color="#2A2A2A",
                      command=lambda: webbrowser.open("file://" + os.path.abspath(ruta_txt))).pack(side="left", padx=5)

        ctk.CTkButton(self.contenedor, text="CERRAR", height=42, font=("Helvetica", 13, "bold"),
                      fg_color=self.app.color_acento_verde, hover_color="#059669",
                      command=self.destroy).pack(fill="x", padx=40, pady=(15, 20))

    def _limpiar(self):
        for w in self.contenedor.winfo_children():
            w.destroy()


# ==========================================
# 2. APLICACIÓN PRINCIPAL
# ==========================================
class ChasquiLogApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Chasqui-Log - POS y Logística")
        self.geometry("1250x800")
        self.configure(fg_color="#121212")

        # Paleta de colores
        self.color_panel = "#1E1E1E"
        self.color_acento_verde = "#10B981"
        self.color_acento_azul = "#3B82F6"
        self.color_alerta = "#F59E0B"
        self.color_rojo = "#F87171"
        self.color_texto_secundario = "#A0A0A0"
        self.color_barra_muted = "#3F4A45"

        # Variables de estado
        self.carrito = {}
        self.total_actual = 0.0
        self.metodo_pago = "Efectivo"
        self.estado_conexion = False

        self.crear_navegacion_superior()

        self.contenedor_principal = ctk.CTkFrame(self, fg_color="transparent")
        self.contenedor_principal.pack(fill="both", expand=True, padx=20, pady=10)

        self.pantalla_dashboard = self.crear_pantalla_dashboard()
        self.pantalla_inventario = self.crear_pantalla_inventario()
        self.pantalla_dispensacion = self.crear_pantalla_dispensacion()
        self.pantalla_analisis = self.crear_pantalla_analisis()

        self.mostrar_pantalla("dashboard")
        self.monitorear_conexion()  # arranca el monitor automático de internet

    # ==========================================
    # NAVEGACIÓN Y ESTADO DE CONEXIÓN
    # ==========================================
    def crear_navegacion_superior(self):
        nav_frame = ctk.CTkFrame(self, fg_color=self.color_panel, height=60, corner_radius=15)
        nav_frame.pack(fill="x", padx=20, pady=(20, 0))

        ctk.CTkLabel(nav_frame, text="Chasqui-Log", font=("Helvetica", 20, "bold"), text_color="white").pack(side="left", padx=20, pady=15)

        frame_tabs = ctk.CTkFrame(nav_frame, fg_color="transparent")
        self.btn_dash = ctk.CTkButton(frame_tabs, text="DASHBOARD", fg_color="transparent", text_color=self.color_acento_verde, hover_color="#2A2A2A", command=lambda: self.mostrar_pantalla("dashboard"))
        self.btn_inv = ctk.CTkButton(frame_tabs, text="INVENTARIO", fg_color="transparent", text_color=self.color_texto_secundario, hover_color="#2A2A2A", command=lambda: self.mostrar_pantalla("inventario"))
        self.btn_disp = ctk.CTkButton(frame_tabs, text="VENTAS", fg_color="transparent", text_color=self.color_texto_secundario, hover_color="#2A2A2A", command=lambda: self.mostrar_pantalla("dispensacion"))
        self.btn_analisis = ctk.CTkButton(frame_tabs, text="ANÁLISIS (GEMMA)", fg_color="transparent", text_color=self.color_texto_secundario, hover_color="#2A2A2A", command=lambda: self.mostrar_pantalla("analisis"))

        self.btn_dash.pack(side="left", padx=5)
        self.btn_inv.pack(side="left", padx=5)
        self.btn_disp.pack(side="left", padx=5)
        self.btn_analisis.pack(side="left", padx=5)
        frame_tabs.place(relx=0.5, rely=0.5, anchor="center")

        # Insignia de conexión: cambia sola entre ONLINE y OFFLINE. Clic = revisar ahora.
        self.badge_estado = ctk.CTkLabel(
            nav_frame, text="⛔ MODO OFFLINE", font=("Helvetica", 11, "bold"),
            text_color=self.color_alerta, fg_color="#2A2118", corner_radius=15,
            width=185, height=30, cursor="hand2")
        self.badge_estado.pack(side="right", padx=20)
        self.badge_estado.bind("<Button-1>", lambda e: self.monitorear_conexion(forzar=True))

    def monitorear_conexion(self, forzar=False):
        """Revisa internet cada 20 s en segundo plano. Si hay señal, vacía la cola de envíos."""
        threading.Thread(target=self._worker_conexion, daemon=True).start()
        if not forzar:
            self.after(20000, self.monitorear_conexion)

    def _worker_conexion(self):
        online = hay_internet()
        if online:
            procesar_cola()
        pendientes = contar_pendientes()
        self.after(0, lambda: self._pintar_badge(online, pendientes))

    def _pintar_badge(self, online, pendientes):
        self.estado_conexion = online
        sufijo = f" ({pendientes} en cola)" if pendientes else ""
        if online:
            self.badge_estado.configure(text="🌐 MODO ONLINE" + sufijo,
                                        text_color=self.color_acento_verde, fg_color="#0F2A20")
        else:
            self.badge_estado.configure(text="⛔ MODO OFFLINE" + sufijo,
                                        text_color=self.color_alerta, fg_color="#2A2118")

    def mostrar_pantalla(self, nombre):
        self.pantalla_dashboard.pack_forget()
        self.pantalla_inventario.pack_forget()
        self.pantalla_dispensacion.pack_forget()
        self.pantalla_analisis.pack_forget()

        botones = {"dashboard": self.btn_dash, "inventario": self.btn_inv,
                   "dispensacion": self.btn_disp, "analisis": self.btn_analisis}
        for clave, boton in botones.items():
            boton.configure(text_color=self.color_acento_verde if clave == nombre else self.color_texto_secundario)

        if nombre == "dashboard":
            self.pantalla_dashboard.pack(fill="both", expand=True)
            self.actualizar_dashboard()
        elif nombre == "inventario":
            self.pantalla_inventario.pack(fill="both", expand=True)
            self.actualizar_lista_inventario()
        elif nombre == "dispensacion":
            self.pantalla_dispensacion.pack(fill="both", expand=True)
            self.actualizar_carrito_ui()
        elif nombre == "analisis":
            self.pantalla_analisis.pack(fill="both", expand=True)
            self.actualizar_analisis()

    # ==========================================
    # UTILIDAD: barras verticales
    # ==========================================
    def dibujar_barras_verticales(self, contenedor, datos, alto_max=170):
        for widget in contenedor.winfo_children():
            widget.destroy()

        if not datos:
            vacio = ctk.CTkFrame(contenedor, fg_color="transparent")
            vacio.pack(expand=True, fill="both")
            ctk.CTkLabel(vacio, text="📦", font=("Helvetica", 32)).pack(pady=(30, 8))
            ctk.CTkLabel(vacio, text="Sin ventas registradas hoy",
                         font=("Helvetica", 13), text_color=self.color_texto_secundario).pack()
            return

        # La query ya garantiza máx. 5 resultados, ordenados DESC por ventas_hoy
        valor_max = max(v for _, v in datos) or 1
        colores   = [self.color_acento_verde, self.color_acento_azul,
                     "#8B5CF6", "#EC4899", "#F59E0B"]
        medallas  = ["🥇", "🥈", "🥉", "4º", "5º"]

        for i, (etiqueta, valor) in enumerate(datos):
            color = colores[i % len(colores)]
            pct   = valor / valor_max          # 0.0 – 1.0

            # tarjeta de altura fija para que no se estire
            tarjeta = ctk.CTkFrame(contenedor, fg_color="#161616", corner_radius=10, height=58)
            tarjeta.pack(fill="x", pady=4, padx=2)
            tarjeta.pack_propagate(False)

            # ① badge a la DERECHA primero (para que reserve su espacio)
            badge = ctk.CTkFrame(tarjeta, fg_color=color, corner_radius=8, width=56, height=42)
            badge.pack(side="right", padx=10, pady=8)
            badge.pack_propagate(False)
            ctk.CTkLabel(badge, text=str(valor), font=("Helvetica", 14, "bold"),
                         text_color="#0A0A0A").pack(pady=(4, 0))
            ctk.CTkLabel(badge, text="uds", font=("Helvetica", 8),
                         text_color="#0A0A0A").pack()

            # ② medalla a la izquierda
            ctk.CTkLabel(tarjeta, text=medallas[i], font=("Helvetica", 16),
                         width=34).pack(side="left", padx=(10, 4))

            # ③ columna central: nombre + barra proporcional
            centro = ctk.CTkFrame(tarjeta, fg_color="transparent")
            centro.pack(side="left", fill="both", expand=True, padx=(4, 8), pady=8)

            ctk.CTkLabel(centro, text=etiqueta, font=("Helvetica", 12, "bold"),
                         text_color="white", anchor="w").pack(fill="x")

            pista = ctk.CTkFrame(centro, fg_color="#2A2A2A", height=7, corner_radius=4)
            pista.pack(fill="x", pady=(5, 0))
            pista.pack_propagate(False)
            # relleno proporcional (mínimo 3% para que siempre sea visible)
            ctk.CTkFrame(pista, fg_color=color, height=7, corner_radius=4).place(
                relx=0, rely=0, relwidth=max(pct, 0.03), relheight=1.0)

    # ==========================================
    # PANTALLA 0: DASHBOARD
    # ==========================================
    def crear_pantalla_dashboard(self):
        frame = ctk.CTkFrame(self.contenedor_principal, fg_color="transparent")
        frame.grid_columnconfigure(0, weight=3)
        frame.grid_columnconfigure(1, weight=2)
        frame.grid_rowconfigure(0, weight=1)
        frame.grid_rowconfigure(1, weight=1)

        panel_top = ctk.CTkFrame(frame, fg_color=self.color_panel, corner_radius=15)
        panel_top.grid(row=0, column=0, sticky="nsew", padx=(0, 10), pady=(0, 10))
        ctk.CTkLabel(panel_top, text="MÁS SOLICITADOS", font=("Helvetica", 14, "bold"), text_color="white").pack(anchor="w", padx=20, pady=(20, 5))
        self.frame_barras_dashboard = ctk.CTkFrame(panel_top, fg_color="transparent")
        self.frame_barras_dashboard.pack(fill="both", expand=True, padx=20, pady=(0, 20))

        panel_stock = ctk.CTkFrame(frame, fg_color=self.color_panel, corner_radius=15)
        panel_stock.grid(row=0, column=1, sticky="nsew", pady=(0, 10))
        cab_stock = ctk.CTkFrame(panel_stock, fg_color="transparent")
        cab_stock.pack(fill="x", padx=20, pady=(20, 5))
        ctk.CTkLabel(cab_stock, text="STOCK CRÍTICO", font=("Helvetica", 14, "bold"), text_color="white").pack(side="left")
        ctk.CTkLabel(cab_stock, text="⚠️", font=("Helvetica", 14)).pack(side="right")
        self.frame_stock_critico = ctk.CTkScrollableFrame(panel_stock, fg_color="transparent")
        self.frame_stock_critico.pack(fill="both", expand=True, padx=15, pady=(0, 15))

        panel_informe = ctk.CTkFrame(frame, fg_color=self.color_panel, corner_radius=15)
        panel_informe.grid(row=1, column=0, sticky="nsew", padx=(0, 10))
        ctk.CTkLabel(panel_informe, text="INFORME DEL TURNO", font=("Helvetica", 14, "bold"), text_color="white").pack(anchor="w", padx=20, pady=(20, 10))
        self.frame_informe_turno = ctk.CTkScrollableFrame(panel_informe, fg_color="transparent")
        self.frame_informe_turno.pack(fill="both", expand=True, padx=15, pady=(0, 15))
        self.lbl_informe_turno = ctk.CTkLabel(self.frame_informe_turno, text="Generando análisis con Gemma",
                                              font=("Helvetica", 13), text_color=self.color_texto_secundario,
                                              justify="left", anchor="nw", wraplength=400)
        self.lbl_informe_turno.pack(fill="both", expand=True, padx=5, pady=5)

        panel_alerta = ctk.CTkFrame(frame, fg_color="#2A2118", corner_radius=15, border_width=1, border_color=self.color_alerta)
        panel_alerta.grid(row=1, column=1, sticky="nsew")
        cab_alerta = ctk.CTkFrame(panel_alerta, fg_color="transparent")
        cab_alerta.pack(fill="x", padx=20, pady=(20, 5))
        ctk.CTkLabel(cab_alerta, text="📣", font=("Helvetica", 16)).pack(side="left")
        ctk.CTkLabel(cab_alerta, text="ALERTA PREDICTIVA", font=("Helvetica", 13, "bold"), text_color=self.color_alerta).pack(side="left", padx=(8, 0))
        self.frame_alerta_predictiva = ctk.CTkScrollableFrame(panel_alerta, fg_color="transparent")
        self.frame_alerta_predictiva.pack(fill="both", expand=True, padx=15, pady=(0, 15))
        self.lbl_alerta_predictiva = ctk.CTkLabel(self.frame_alerta_predictiva, text="Calculando proyección de quiebre de stock con Gemma...",
                                                  font=("Helvetica", 13), text_color="white", justify="left", anchor="nw", wraplength=340)
        self.lbl_alerta_predictiva.pack(fill="both", expand=True, padx=5, pady=5)

        return frame

    def actualizar_dashboard(self):
        conexion = sqlite3.connect(DB_NAME)
        cursor = conexion.cursor()
        cursor.execute("SELECT nombre, ventas_hoy FROM productos WHERE ventas_hoy > 0 ORDER BY ventas_hoy DESC LIMIT 5")
        top_vendidos = cursor.fetchall()
        cursor.execute("SELECT nombre, stock_actual, stock_minimo FROM productos WHERE stock_actual <= stock_minimo ORDER BY stock_actual ASC")
        criticos = cursor.fetchall()
        conexion.close()

        self.dibujar_barras_verticales(self.frame_barras_dashboard, top_vendidos)

        for widget in self.frame_stock_critico.winfo_children():
            widget.destroy()
        if not criticos:
            vacio = ctk.CTkFrame(self.frame_stock_critico, fg_color="transparent")
            vacio.pack(expand=True, fill="both")
            ctk.CTkLabel(vacio, text="✅", font=("Helvetica", 28)).pack(pady=(20, 6))
            ctk.CTkLabel(vacio, text="Todo el stock en niveles seguros",
                         font=("Helvetica", 12), text_color=self.color_texto_secundario).pack()
        else:
            for nombre, stock, minimo in criticos:
                # calcular urgencia
                pct_stock = min(stock / minimo, 1.0) if minimo > 0 else 1.0
                if stock == 0:
                    color_urgencia = self.color_rojo
                    etiqueta_nivel = "AGOTADO"
                elif pct_stock <= 0.3:
                    color_urgencia = self.color_alerta
                    etiqueta_nivel = "CRÍTICO"
                else:
                    color_urgencia = "#F59E0B"
                    etiqueta_nivel = "BAJO"

                tarjeta = ctk.CTkFrame(self.frame_stock_critico, fg_color="#1A1A1A", corner_radius=10,
                                       border_width=1, border_color=color_urgencia)
                tarjeta.pack(fill="x", pady=4, padx=2)

                # cabecera: nombre + badge de nivel
                cab = ctk.CTkFrame(tarjeta, fg_color="transparent")
                cab.pack(fill="x", padx=10, pady=(8, 2))
                ctk.CTkLabel(cab, text=nombre, font=("Helvetica", 12, "bold"),
                             text_color="white", anchor="w").pack(side="left", fill="x", expand=True)
                badge = ctk.CTkFrame(cab, fg_color=color_urgencia, corner_radius=6, width=62, height=20)
                badge.pack(side="right")
                badge.pack_propagate(False)
                ctk.CTkLabel(badge, text=etiqueta_nivel, font=("Helvetica", 9, "bold"),
                             text_color="#0A0A0A").pack(expand=True)

                # fila stock actual / mínimo
                fila_nums = ctk.CTkFrame(tarjeta, fg_color="transparent")
                fila_nums.pack(fill="x", padx=10, pady=(0, 4))
                ctk.CTkLabel(fila_nums, text=f"Stock: ", font=("Helvetica", 11),
                             text_color=self.color_texto_secundario).pack(side="left")
                ctk.CTkLabel(fila_nums, text=str(stock), font=("Helvetica", 13, "bold"),
                             text_color=color_urgencia).pack(side="left")
                ctk.CTkLabel(fila_nums, text=f" / {minimo} mínimo", font=("Helvetica", 11),
                             text_color=self.color_texto_secundario).pack(side="left")

                # barra de urgencia visual
                pista = ctk.CTkFrame(tarjeta, fg_color="#2A2A2A", height=6, corner_radius=3)
                pista.pack(fill="x", padx=10, pady=(0, 8))
                pista.pack_propagate(False)
                ctk.CTkFrame(pista, fg_color=color_urgencia, height=6,
                             corner_radius=3).place(relx=0, rely=0, relwidth=pct_stock, relheight=1.0)

        for w in self.frame_informe_turno.winfo_children():
            w.destroy()
        self.lbl_informe_turno = ctk.CTkLabel(self.frame_informe_turno, text="Generando análisis con Gemma",
                                              font=("Helvetica", 13), text_color=self.color_texto_secundario,
                                              justify="left", anchor="nw", wraplength=400)
        self.lbl_informe_turno.pack(fill="both", expand=True, padx=5, pady=5)
        for w in self.frame_alerta_predictiva.winfo_children():
            w.destroy()
        self.lbl_alerta_predictiva = ctk.CTkLabel(self.frame_alerta_predictiva, text="Calculando proyección de quiebre de stock con Gemma...",
                                                  font=("Helvetica", 13), text_color="white", justify="left", anchor="nw", wraplength=340)
        self.lbl_alerta_predictiva.pack(fill="both", expand=True, padx=5, pady=5)

        datos = {"top_vendidos": top_vendidos, "criticos": criticos}
        threading.Thread(target=self._worker_informe_turno, args=(datos,), daemon=True).start()
        threading.Thread(target=self._worker_alerta_predictiva, args=(datos,), daemon=True).start()

    def _worker_informe_turno(self, datos):
        top_txt = ", ".join(f"{n} ({v} unidades)" for n, v in datos["top_vendidos"]) or "sin ventas registradas todavía"
        criticos_txt = ", ".join(f"{n} ({s} uds, mínimo {m})" for n, s, m in datos["criticos"]) or "ninguno"
        prompt = (
            "Eres el sistema de análisis de una posta médica rural en Perú, sin conexión a internet. "
            "Con los datos reales de hoy (no inventes cifras que no te doy), redacta un informe breve "
            "de 3 a 4 líneas para el encargado del turno, en español, tono profesional y directo.\n\n"
            f"Medicamentos más solicitados hoy: {top_txt}.\n"
            f"Medicamentos con stock crítico (en o bajo el mínimo): {criticos_txt}.\n\n"
            "Menciona el patrón de consumo y recomienda una acción concreta sobre el reabastecimiento.")
        resultado = llamar_gemma(prompt)
        if resultado is None:
            self.after(0, lambda: self._mostrar_informe_offline(datos))
        else:
            self.after(0, lambda: self._mostrar_informe_texto(resultado))

    def _mostrar_informe_texto(self, texto):
        """Muestra la respuesta de Gemma como texto en el panel de informe del turno."""
        for w in self.frame_informe_turno.winfo_children():
            w.destroy()
        lbl = ctk.CTkLabel(self.frame_informe_turno, text=texto, font=("Helvetica", 13),
                           text_color="white", justify="left", anchor="nw", wraplength=400)
        lbl.pack(fill="both", expand=True, padx=5, pady=5)

    def _mostrar_informe_offline(self, datos):
        """Construye tarjetas estructuradas para el informe del turno cuando Gemma no está disponible."""
        for w in self.frame_informe_turno.winfo_children():
            w.destroy()

        COLOR_TARJETA = "#1E1E1E"
        COLOR_SEC     = self.color_texto_secundario
        COLOR_ALERTA  = self.color_alerta
        COLOR_VERDE   = self.color_acento_verde

        # --- Título ---
        titulo_frame = ctk.CTkFrame(self.frame_informe_turno, fg_color="transparent")
        titulo_frame.pack(fill="x", pady=(4, 8))
        ctk.CTkLabel(titulo_frame, text="📋", font=("Helvetica", 18)).pack(side="left")
        ctk.CTkLabel(titulo_frame, text=" Informe del turno",
                     font=("Helvetica", 14, "bold"), text_color="white").pack(side="left")

        # --- Más solicitados ---
        ctk.CTkLabel(self.frame_informe_turno, text="🔹  Más solicitados hoy",
                     font=("Helvetica", 11, "bold"), text_color=COLOR_SEC, anchor="w").pack(anchor="w", padx=2, pady=(4, 2))
        if datos["top_vendidos"]:
            for nombre, ventas in datos["top_vendidos"]:
                fila = ctk.CTkFrame(self.frame_informe_turno, fg_color=COLOR_TARJETA, corner_radius=8)
                fila.pack(fill="x", pady=2, padx=2)
                ctk.CTkLabel(fila, text=nombre, font=("Helvetica", 12), text_color="white").pack(side="left", padx=10, pady=7)
                ctk.CTkLabel(fila, text=f"{ventas} uds", font=("Helvetica", 11, "bold"),
                             text_color=COLOR_VERDE).pack(side="right", padx=10)
        else:
            ctk.CTkLabel(self.frame_informe_turno, text="Sin ventas registradas aún",
                         font=("Helvetica", 12), text_color=COLOR_SEC).pack(anchor="w", padx=10)

        # --- Separador ---
        ctk.CTkFrame(self.frame_informe_turno, fg_color="#2A2A2A", height=1).pack(fill="x", pady=(8, 4), padx=2)

        # --- Stock crítico ---
        ctk.CTkLabel(self.frame_informe_turno, text="⚠️  Stock crítico",
                     font=("Helvetica", 11, "bold"), text_color=COLOR_SEC, anchor="w").pack(anchor="w", padx=2, pady=(0, 2))
        if datos["criticos"]:
            for nombre, stock, minimo in datos["criticos"]:
                fila = ctk.CTkFrame(self.frame_informe_turno, fg_color=COLOR_TARJETA, corner_radius=8)
                fila.pack(fill="x", pady=2, padx=2)
                ctk.CTkLabel(fila, text=nombre, font=("Helvetica", 12), text_color="white").pack(side="left", padx=10, pady=7)
                ctk.CTkLabel(fila, text=f"{stock} / {minimo} mínimo", font=("Helvetica", 11),
                             text_color=COLOR_ALERTA).pack(side="right", padx=10)
        else:
            ctk.CTkLabel(self.frame_informe_turno, text="Sin productos en estado crítico",
                         font=("Helvetica", 12), text_color=COLOR_SEC).pack(anchor="w", padx=10)

        # --- Recomendación ---
        ctk.CTkFrame(self.frame_informe_turno, fg_color="#2A2A2A", height=1).pack(fill="x", pady=(8, 4), padx=2)
        rec = ctk.CTkFrame(self.frame_informe_turno, fg_color="#1A2A1A", corner_radius=8)
        rec.pack(fill="x", pady=2, padx=2)
        ctk.CTkLabel(rec, text="📦  Preparar pedido de reposición", font=("Helvetica", 12, "bold"),
                     text_color=COLOR_VERDE).pack(anchor="w", padx=12, pady=8)

    def _worker_alerta_predictiva(self, datos):
        """Worker enriquecido: obtiene clima + geolocalización + mercados y genera
        una recomendación de día/hora óptimo para reabastecer."""
        if not datos["criticos"]:
            self.after(0, lambda: self._mostrar_alerta_sin_criticos())
            return

        criticos_txt = ", ".join(
            f"{n}: {s} unidades (mínimo {m})" for n, s, m in datos["criticos"])
        p_urgente = min(datos["criticos"], key=lambda x: x[1])

        # --- Intentar obtener datos de internet ---
        ubicacion   = None
        pronostico  = []
        mercados    = []
        tiene_net   = hay_internet()

        if tiene_net:
            ubicacion  = obtener_ubicacion_ip()
            if ubicacion:
                pronostico = obtener_pronostico_clima(ubicacion["lat"], ubicacion["lon"])
                mercados   = obtener_mercados_cercanos(ubicacion["lat"], ubicacion["lon"])

        # --- Construir prompt enriquecido ---
        hoy = datetime.now().strftime("%A %d de %B de %Y")
        seccion_clima = ""
        dias_buenos   = []
        dias_malos    = []
        if pronostico:
            resumen_dias = []
            for d in pronostico:
                estado = "⚠️ MALO (lluvia/tormenta)" if d["malo"] else "✅ Bueno"
                resumen_dias.append(
                    f"  {d['nombre_dia']} {d['dia_num']}: {d['desc']}, "
                    f"lluvia {d['lluvia_mm']}mm, viento {d['viento_kmh']}km/h — {estado}")
                if d["malo"]:
                    dias_malos.append(f"{d['nombre_dia']} {d['dia_num']}")
                else:
                    dias_buenos.append(f"{d['nombre_dia']} {d['dia_num']}")
            seccion_clima = "Pronóstico climático próximos 7 días:\n" + "\n".join(resumen_dias)

        seccion_lugares = ""
        if mercados:
            lista_m = "\n".join(
                f"  - {m['nombre']} ({m['tipo']}) a {m['dist_km']} km"
                for m in mercados[:5])
            seccion_lugares = f"Lugares cercanos para comprar (OpenStreetMap):\n{lista_m}"

        seccion_ubicacion = ""
        if ubicacion:
            seccion_ubicacion = (
                f"Ubicación detectada: {ubicacion['ciudad']}, "
                f"{ubicacion['region']}, {ubicacion['pais']}")

        prompt = (
            "Eres el asesor logístico de una posta médica rural en Perú. "
            "El responsable de la posta vive en zona rural y debe viajar en bus a la ciudad "
            "o mercado más cercano para comprar medicamentos. El viaje puede verse afectado "
            "por lluvias fuertes, tormentas o vientos que bloquean caminos rurales.\n\n"
            f"Fecha actual: {hoy}\n"
            f"Stock crítico urgente: {criticos_txt}\n"
            f"{seccion_ubicacion}\n"
            f"{seccion_clima}\n"
            f"{seccion_lugares}\n\n"
            "Con esta información REAL, redacta en español un mensaje breve (máx 5 líneas) que:\n"
            "1. Indique cuál es el MEJOR DÍA de esta semana para ir a comprar (considera el clima).\n"
            "2. Sugiera a qué hora salir (mañana temprano si puede, antes de lluvias).\n"
            "3. Mencione los lugares más cercanos donde puede comprar.\n"
            "4. Advierta sobre los días con mal clima que debe evitar.\n"
            "Sé directo, práctico y en tono amigable. No inventes datos que no te di."
        )

        resultado_gemma = llamar_gemma(prompt)
        self.after(0, lambda: self._mostrar_alerta_completa(
            resultado_gemma, p_urgente, datos["criticos"],
            pronostico, mercados, ubicacion
        ))

    def _mostrar_alerta_sin_criticos(self):
        """Panel cuando no hay stock crítico."""
        for w in self.frame_alerta_predictiva.winfo_children():
            w.destroy()
        ctk.CTkLabel(self.frame_alerta_predictiva, text="✅", font=("Helvetica", 28)).pack(pady=(20, 6))
        ctk.CTkLabel(self.frame_alerta_predictiva, text="Sin productos en estado crítico",
                     font=("Helvetica", 12), text_color=self.color_texto_secundario).pack()

    def _mostrar_alerta_completa(self, texto_gemma, p_urgente, todos_criticos,
                                  pronostico, mercados, ubicacion):
        """Renderiza el panel de alerta predictiva completo con clima, IA y mercados."""
        for w in self.frame_alerta_predictiva.winfo_children():
            w.destroy()

        C_ALERTA  = self.color_alerta
        C_ROJO    = self.color_rojo
        C_VERDE   = self.color_acento_verde
        C_AZUL    = self.color_acento_azul
        C_SEC     = self.color_texto_secundario
        C_PANEL   = "#1E1E1E"
        C_URGENTE = "#2E1A10"

        def sep(color="#2A2A2A"):
            ctk.CTkFrame(self.frame_alerta_predictiva,
                         fg_color=color, height=1).pack(fill="x", pady=(6, 4), padx=2)

        # ══════════════════════════════════════════
        # 1. CABECERA: stock más urgente
        # ══════════════════════════════════════════
        cab = ctk.CTkFrame(self.frame_alerta_predictiva, fg_color=C_URGENTE,
                           corner_radius=10, border_width=1, border_color=C_ALERTA)
        cab.pack(fill="x", pady=(2, 4), padx=2)
        ctk.CTkLabel(cab, text="🚨  Más urgente", font=("Helvetica", 9, "bold"),
                     text_color=C_ALERTA).pack(anchor="w", padx=12, pady=(8, 0))
        ctk.CTkLabel(cab, text=p_urgente[0], font=("Helvetica", 14, "bold"),
                     text_color="white").pack(anchor="w", padx=12)
        fila_s = ctk.CTkFrame(cab, fg_color="transparent")
        fila_s.pack(fill="x", padx=12, pady=(2, 10))
        ctk.CTkLabel(fila_s, text=f"Stock: {p_urgente[1]} uds",
                     font=("Helvetica", 11), text_color=C_ALERTA).pack(side="left")
        ctk.CTkLabel(fila_s, text=f"Mínimo: {p_urgente[2]} uds",
                     font=("Helvetica", 11), text_color=C_SEC).pack(side="right")

        # otros críticos
        otros = [x for x in todos_criticos if x[0] != p_urgente[0]]
        if otros:
            ctk.CTkLabel(self.frame_alerta_predictiva, text="Otros productos críticos",
                         font=("Helvetica", 10, "bold"), text_color=C_SEC).pack(anchor="w", padx=4, pady=(4, 2))
            for nombre, stock, minimo in otros:
                f = ctk.CTkFrame(self.frame_alerta_predictiva, fg_color=C_PANEL, corner_radius=7)
                f.pack(fill="x", pady=2, padx=2)
                ctk.CTkLabel(f, text=nombre, font=("Helvetica", 11),
                             text_color="white").pack(side="left", padx=10, pady=6)
                ctk.CTkLabel(f, text=f"{stock}/{minimo}", font=("Helvetica", 11),
                             text_color=C_ALERTA).pack(side="right", padx=10)

        # ══════════════════════════════════════════
        # 2. RECOMENDACIÓN DE GEMMA (si disponible)
        # ══════════════════════════════════════════
        sep()
        if texto_gemma:
            ctk.CTkLabel(self.frame_alerta_predictiva,
                         text="🤖  Recomendación de Gemma",
                         font=("Helvetica", 11, "bold"), text_color=C_VERDE).pack(anchor="w", padx=4, pady=(0, 4))
            burbuja = ctk.CTkFrame(self.frame_alerta_predictiva,
                                   fg_color="#0F2A20", corner_radius=10,
                                   border_width=1, border_color=C_VERDE)
            burbuja.pack(fill="x", padx=2, pady=(0, 4))
            ctk.CTkLabel(burbuja, text=texto_gemma, font=("Helvetica", 12),
                         text_color="white", justify="left", anchor="nw",
                         wraplength=310).pack(anchor="w", padx=12, pady=10)
        else:
            aviso = ctk.CTkFrame(self.frame_alerta_predictiva, fg_color=C_URGENTE,
                                 corner_radius=8, border_width=1, border_color=C_ALERTA)
            aviso.pack(fill="x", padx=2, pady=(0, 4))
            ctk.CTkLabel(aviso, text="⚠️  Priorizar reabastecimiento de inmediato",
                         font=("Helvetica", 11, "bold"),
                         text_color=C_ALERTA).pack(anchor="w", padx=12, pady=8)

        # ══════════════════════════════════════════
        # 3. PRONÓSTICO CLIMÁTICO (7 días)
        # ══════════════════════════════════════════
        if pronostico:
            sep()
            # ubicación
            if ubicacion:
                ctk.CTkLabel(self.frame_alerta_predictiva,
                             text=f"📍 {ubicacion['ciudad']}, {ubicacion['region']}",
                             font=("Helvetica", 10), text_color=C_SEC).pack(anchor="w", padx=4)
            ctk.CTkLabel(self.frame_alerta_predictiva,
                         text="🌤  Pronóstico próximos 7 días",
                         font=("Helvetica", 11, "bold"), text_color="white").pack(anchor="w", padx=4, pady=(4, 6))

            # fila de chips de días
            grid_dias = ctk.CTkFrame(self.frame_alerta_predictiva, fg_color="transparent")
            grid_dias.pack(fill="x", padx=2)
            for idx, d in enumerate(pronostico):
                color_chip = "#2E1A10" if d["malo"] else "#0F1E0F"
                borde_chip = C_ALERTA if d["malo"] else C_VERDE
                chip = ctk.CTkFrame(grid_dias, fg_color=color_chip, corner_radius=8,
                                    border_width=1, border_color=borde_chip, width=46)
                chip.grid(row=0, column=idx, padx=2, pady=2, sticky="nsew")
                chip.grid_propagate(False)
                grid_dias.grid_columnconfigure(idx, weight=1)
                ctk.CTkLabel(chip, text=d["nombre_dia"], font=("Helvetica", 8, "bold"),
                             text_color=C_ALERTA if d["malo"] else C_VERDE).pack(pady=(5, 0))
                ctk.CTkLabel(chip, text=d["emoji"], font=("Helvetica", 14)).pack()
                ctk.CTkLabel(chip, text=f"{d['lluvia_mm']}mm",
                             font=("Helvetica", 7), text_color=C_SEC).pack(pady=(0, 4))

            # leyenda
            leyenda = ctk.CTkFrame(self.frame_alerta_predictiva, fg_color="transparent")
            leyenda.pack(fill="x", pady=(4, 0), padx=4)
            ctk.CTkLabel(leyenda, text="🟢 Buen día para viajar",
                         font=("Helvetica", 9), text_color=C_VERDE).pack(side="left")
            ctk.CTkLabel(leyenda, text="🔴 Evitar (mal clima)",
                         font=("Helvetica", 9), text_color=C_ALERTA).pack(side="right")

        elif not hay_internet():
            sep()
            no_net = ctk.CTkFrame(self.frame_alerta_predictiva, fg_color=C_PANEL,
                                  corner_radius=8)
            no_net.pack(fill="x", padx=2, pady=2)
            ctk.CTkLabel(no_net, text="📡  Sin conexión — pronóstico no disponible",
                         font=("Helvetica", 11), text_color=C_SEC).pack(padx=12, pady=8)

        # ══════════════════════════════════════════
        # 4. LUGARES CERCANOS PARA COMPRAR
        # ══════════════════════════════════════════
        if mercados:
            sep()
            ctk.CTkLabel(self.frame_alerta_predictiva,
                         text="🏪  Lugares cercanos para comprar",
                         font=("Helvetica", 11, "bold"), text_color="white").pack(anchor="w", padx=4, pady=(0, 4))
            for m in mercados[:5]:
                fm = ctk.CTkFrame(self.frame_alerta_predictiva,
                                  fg_color=C_PANEL, corner_radius=8)
                fm.pack(fill="x", pady=2, padx=2)
                ctk.CTkLabel(fm, text=f"{m['icono']}  {m['nombre']}",
                             font=("Helvetica", 11), text_color="white").pack(side="left", padx=10, pady=6)
                dist_color = C_VERDE if m["dist_km"] < 15 else C_ALERTA
                ctk.CTkLabel(fm, text=f"{m['dist_km']} km",
                             font=("Helvetica", 11, "bold"),
                             text_color=dist_color).pack(side="right", padx=10)

    # ==========================================
    # PANTALLA 1: INVENTARIO
    # ==========================================
    def crear_pantalla_inventario(self):
        frame = ctk.CTkFrame(self.contenedor_principal, fg_color="transparent")
        frame.grid_columnconfigure(0, weight=3)
        frame.grid_columnconfigure(1, weight=1)
        frame.grid_rowconfigure(0, weight=1)

        frame_izq = ctk.CTkFrame(frame, fg_color=self.color_panel, corner_radius=15)
        frame_izq.grid(row=0, column=0, sticky="nsew", padx=(0, 10))

        frame_busqueda = ctk.CTkFrame(frame_izq, fg_color="transparent")
        frame_busqueda.pack(fill="x", padx=20, pady=(20, 10))
        self.ent_buscador_inventario = ctk.CTkEntry(frame_busqueda, placeholder_text="🔍 Buscar medicamento...",
                                                    height=40, fg_color="#121212", border_width=0, corner_radius=8)
        self.ent_buscador_inventario.pack(fill="x")
        self.ent_buscador_inventario.bind("<KeyRelease>", self.buscar_inventario)

        encabezados = ctk.CTkFrame(frame_izq, fg_color="transparent")
        encabezados.pack(fill="x", padx=20, pady=(0, 10))
        ctk.CTkLabel(encabezados, text="ID | MEDICAMENTO", font=("Helvetica", 11, "bold"), text_color=self.color_texto_secundario).pack(side="left")
        ctk.CTkLabel(encabezados, text="PRECIO", font=("Helvetica", 11, "bold"), text_color=self.color_texto_secundario, width=80).pack(side="right", padx=20)
        ctk.CTkLabel(encabezados, text="CANTIDAD", font=("Helvetica", 11, "bold"), text_color=self.color_texto_secundario, width=80).pack(side="right", padx=20)

        self.lista_inventario_ui = ctk.CTkScrollableFrame(frame_izq, fg_color="transparent")
        self.lista_inventario_ui.pack(fill="both", expand=True, padx=10, pady=10)

        frame_der = ctk.CTkFrame(frame, fg_color=self.color_panel, corner_radius=15)
        frame_der.grid(row=0, column=1, sticky="nsew")

        ctk.CTkLabel(frame_der, text="➕ AGREGAR INGRESO", font=("Helvetica", 14, "bold")).pack(anchor="w", padx=20, pady=20)

        ctk.CTkLabel(frame_der, text="Nombre del fármaco", text_color=self.color_texto_secundario).pack(anchor="w", padx=20, pady=(10, 0))
        self.ent_nombre = ctk.CTkEntry(frame_der, fg_color="#121212", border_width=0, height=35)
        self.ent_nombre.pack(fill="x", padx=20, pady=5)

        ctk.CTkLabel(frame_der, text="Cantidad", text_color=self.color_texto_secundario).pack(anchor="w", padx=20, pady=(10, 0))
        self.ent_cantidad = ctk.CTkEntry(frame_der, fg_color="#121212", border_width=0, height=35)
        self.ent_cantidad.pack(fill="x", padx=20, pady=5)

        ctk.CTkLabel(frame_der, text="Precio", text_color=self.color_texto_secundario).pack(anchor="w", padx=20, pady=(10, 0))
        self.ent_precio = ctk.CTkEntry(frame_der, fg_color="#121212", border_width=0, height=35, placeholder_text="S/")
        self.ent_precio.pack(fill="x", padx=20, pady=5)

        ctk.CTkButton(frame_der, text="💾 AGREGAR", fg_color=self.color_acento_verde, hover_color="#059669",
                      height=40, font=("Helvetica", 12, "bold"), command=self.guardar_producto_bd).pack(fill="x", padx=20, pady=30)

        return frame

    def guardar_producto_bd(self):
        nom, cant, prec = self.ent_nombre.get().strip(), self.ent_cantidad.get().strip(), self.ent_precio.get().strip()
        if not (nom and cant and prec):
            return messagebox.showwarning("Error", "Campos vacíos")
        try:
            cant_val = int(cant)
            prec_val = float(prec)
        except ValueError:
            return messagebox.showwarning("Error", "Cantidad y precio deben ser números válidos")
        if cant_val < 0 or prec_val < 0:
            return messagebox.showwarning("Error", "La cantidad y el precio no pueden ser negativos")

        conexion = sqlite3.connect(DB_NAME)
        cursor = conexion.cursor()
        cursor.execute("INSERT INTO productos (nombre, stock_actual, precio) VALUES (?, ?, ?)", (nom, cant_val, prec_val))
        conexion.commit()
        conexion.close()

        self.ent_nombre.delete(0, 'end'); self.ent_cantidad.delete(0, 'end'); self.ent_precio.delete(0, 'end')
        self.actualizar_lista_inventario()

    def buscar_inventario(self, event=None):
        self.actualizar_lista_inventario(filtro=self.ent_buscador_inventario.get().strip())

    def actualizar_lista_inventario(self, filtro=""):
        for widget in self.lista_inventario_ui.winfo_children():
            widget.destroy()

        conexion = sqlite3.connect(DB_NAME)
        cursor = conexion.cursor()
        if filtro:
            cursor.execute("SELECT id, nombre, stock_actual, precio, stock_minimo FROM productos WHERE nombre LIKE ? ORDER BY nombre", (f"%{filtro}%",))
        else:
            cursor.execute("SELECT id, nombre, stock_actual, precio, stock_minimo FROM productos ORDER BY id")
        resultados = cursor.fetchall()
        conexion.close()

        if not resultados:
            ctk.CTkLabel(self.lista_inventario_ui, text="No se encontraron medicamentos", text_color=self.color_texto_secundario).pack(pady=20)
            return

        for p in resultados:
            es_alerta = p[2] <= p[4]
            item = ctk.CTkFrame(self.lista_inventario_ui, fg_color="transparent", cursor="hand2")
            item.pack(fill="x", pady=5)
            ctk.CTkFrame(item, height=1, fg_color="#2A2A2A").pack(fill="x", side="top", pady=(0, 5))

            titulo = f"[{p[0]}] {p[1]} {'⚠️' if es_alerta else ''}"
            lbl_titulo = ctk.CTkLabel(item, text=titulo, font=("Helvetica", 14, "bold"), text_color=self.color_alerta if es_alerta else "white")
            lbl_titulo.pack(side="left", padx=10)
            lbl_precio = ctk.CTkLabel(item, text=f"S/ {p[3]:.2f}", font=("Helvetica", 14))
            lbl_precio.pack(side="right", padx=(20, 10))
            lbl_stock = ctk.CTkLabel(item, text=str(p[2]), font=("Helvetica", 14, "bold"), text_color=self.color_alerta if es_alerta else "white", width=50)
            lbl_stock.pack(side="right", padx=20)

            for widget in (item, lbl_titulo, lbl_precio, lbl_stock):
                widget.bind("<Button-1>", lambda e, id_prod=p[0]: self.mostrar_detalle_producto(id_prod))

    def mostrar_detalle_producto(self, id_prod):
        conexion = sqlite3.connect(DB_NAME)
        cursor = conexion.cursor()
        cursor.execute("SELECT id, nombre, stock_actual, precio, stock_minimo, ventas_hoy FROM productos WHERE id = ?", (id_prod,))
        p = cursor.fetchone()
        conexion.close()
        if not p:
            return

        for widget in self.lista_inventario_ui.winfo_children():
            widget.destroy()

        es_alerta = p[2] <= p[4]
        detalle = ctk.CTkFrame(self.lista_inventario_ui, fg_color="#121212", corner_radius=10)
        detalle.pack(fill="x", padx=10, pady=10)

        ctk.CTkButton(detalle, text="← Volver a la lista", fg_color="transparent",
                      text_color=self.color_acento_verde, hover_color="#2A2A2A",
                      command=self.volver_a_lista_inventario).pack(anchor="w", padx=15, pady=(15, 10))

        ctk.CTkLabel(detalle, text=f"[{p[0]}] {p[1]}", font=("Helvetica", 20, "bold"),
                     text_color=self.color_alerta if es_alerta else "white").pack(anchor="w", padx=15, pady=(0, 15))

        fila_stock = ctk.CTkFrame(detalle, fg_color="transparent")
        fila_stock.pack(fill="x", padx=15, pady=5)
        ctk.CTkLabel(fila_stock, text="Stock actual", text_color=self.color_texto_secundario).pack(side="left")

        frame_stock_ctrl = ctk.CTkFrame(fila_stock, fg_color="transparent")
        frame_stock_ctrl.pack(side="right")

        ctk.CTkButton(frame_stock_ctrl, text="-", width=25, height=25, fg_color="#1E1E1E", hover_color="#2A2A2A",
                      command=lambda: self.ajustar_stock(id_prod, -1)).pack(side="left", padx=2)

        lbl_stock_val = ctk.CTkLabel(frame_stock_ctrl, text=str(p[2]), font=("Helvetica", 13, "bold"),
                                     text_color=self.color_alerta if es_alerta else "white", width=40, cursor="hand2")
        lbl_stock_val.pack(side="left", padx=5)
        lbl_stock_val.bind("<Button-1>", lambda e: self.editar_stock_manual(id_prod, frame_stock_ctrl, lbl_stock_val))

        ctk.CTkButton(frame_stock_ctrl, text="+", width=25, height=25, fg_color="#1E1E1E", hover_color="#2A2A2A",
                      command=lambda: self.ajustar_stock(id_prod, 1)).pack(side="left", padx=2)

        for etiqueta, valor in [("Stock mínimo", str(p[4])), ("Precio", f"S/ {p[3]:.2f}"), ("Vendidos hoy", str(p[5]))]:
            fila = ctk.CTkFrame(detalle, fg_color="transparent")
            fila.pack(fill="x", padx=15, pady=5)
            ctk.CTkLabel(fila, text=etiqueta, text_color=self.color_texto_secundario).pack(side="left")
            ctk.CTkLabel(fila, text=valor, font=("Helvetica", 13, "bold")).pack(side="right")

        if es_alerta:
            ctk.CTkLabel(detalle, text="⚠️ Stock por debajo del mínimo", text_color=self.color_alerta,
                         font=("Helvetica", 12, "bold")).pack(anchor="w", padx=15, pady=(10, 5))
        else:
            ctk.CTkLabel(detalle, text="", height=1).pack(pady=(5, 0))

        ctk.CTkFrame(detalle, height=1, fg_color="#2A2A2A").pack(fill="x", padx=15, pady=(5, 10))
        ctk.CTkButton(detalle, text="🗑  ELIMINAR ARTÍCULO", fg_color="#3B1010", hover_color="#7F1D1D",
                      text_color="#F87171", height=38, corner_radius=8, font=("Helvetica", 12, "bold"),
                      command=lambda: self.eliminar_producto_bd(id_prod, p[1])).pack(fill="x", padx=15, pady=(0, 15))

    def ajustar_stock(self, id_prod, delta):
        conexion = sqlite3.connect(DB_NAME)
        cursor = conexion.cursor()
        cursor.execute("UPDATE productos SET stock_actual = MAX(stock_actual + ?, 0) WHERE id = ?", (delta, id_prod))
        conexion.commit()
        conexion.close()
        self.mostrar_detalle_producto(id_prod)
        self.verificar_stock_critico_producto(id_prod)

    def editar_stock_manual(self, id_prod, frame_stock_ctrl, lbl_stock_val):
        valor_actual = lbl_stock_val.cget("text")
        lbl_stock_val.pack_forget()

        entry_stock = ctk.CTkEntry(frame_stock_ctrl, width=50, height=25)
        entry_stock.insert(0, valor_actual)
        entry_stock.pack(side="left", padx=5)
        entry_stock.focus()
        entry_stock.select_range(0, 'end')

        def guardar(event=None):
            nuevo = entry_stock.get().strip()
            if nuevo.isdigit():
                conexion = sqlite3.connect(DB_NAME)
                cursor = conexion.cursor()
                cursor.execute("UPDATE productos SET stock_actual = ? WHERE id = ?", (int(nuevo), id_prod))
                conexion.commit()
                conexion.close()
            self.mostrar_detalle_producto(id_prod)
            self.verificar_stock_critico_producto(id_prod)

        entry_stock.bind("<Return>", guardar)
        entry_stock.bind("<FocusOut>", guardar)

    def volver_a_lista_inventario(self):
        self.ent_buscador_inventario.delete(0, 'end')
        self.actualizar_lista_inventario()

    def eliminar_producto_bd(self, id_prod, nombre):
        if messagebox.askyesno("Eliminar artículo", f"¿Estás seguro de que deseas eliminar '{nombre}'?\n\nEsta acción no se puede deshacer."):
            conexion = sqlite3.connect(DB_NAME)
            cursor = conexion.cursor()
            cursor.execute("DELETE FROM productos WHERE id = ?", (id_prod,))
            conexion.commit()
            conexion.close()
            self.volver_a_lista_inventario()

    # ==========================================
    # ALERTAS AUTOMÁTICAS DE STOCK (sin preguntar)
    # ==========================================
    def verificar_stock_critico_producto(self, id_prod):
        """Se llama al ajustar stock manualmente: si cayó al mínimo, alerta sola."""
        conexion = sqlite3.connect(DB_NAME)
        cursor = conexion.cursor()
        cursor.execute("SELECT nombre, stock_actual, stock_minimo FROM productos WHERE id = ?", (id_prod,))
        p = cursor.fetchone()
        conexion.close()
        if p and p[1] <= p[2]:
            self.enviar_alerta_stock([p])

    def enviar_alerta_stock(self, alertas):
        """Envía la alerta al dueño AUTOMÁTICAMENTE, sin confirmación del usuario.
        Sin internet, la deja en cola y se envía sola cuando vuelva la señal."""
        if not alertas:
            return
        threading.Thread(target=self._worker_alerta_stock, args=(alertas,), daemon=True).start()

    def _worker_alerta_stock(self, alertas):
        detalle = "\n".join(f"- {n}: {s} uds (minimo {m})" for n, s, m in alertas)
        mensaje = (
            f"ALERTA DE STOCK - Chasqui-Log\n"
            f"Posta {DATOS_POSTA['id_posta']} | {datetime.now().strftime('%d/%m/%Y %H:%M')}\n\n"
            f"Productos en nivel critico:\n{detalle}\n\n"
            f"Se requiere reabastecimiento urgente.")

        if hay_internet():
            exito, detalle_envio = enviar_whatsapp_callmebot(NUM_DUENO, mensaje)
            if not exito:
                encolar_envio("alerta_stock", NUM_DUENO, mensaje)
                estado = f"Alerta encolada ({detalle_envio})"
            else:
                estado = "Alerta enviada al dueño por WhatsApp"
        else:
            encolar_envio("alerta_stock", NUM_DUENO, mensaje)
            estado = "Sin internet: alerta en cola, se enviará al recuperar señal"

        pendientes = contar_pendientes()
        self.after(0, lambda: self._pintar_badge(self.estado_conexion, pendientes))
        print(f"[ALERTA STOCK] {estado}")

    # ==========================================
    # PANTALLA 2: DISPENSACIÓN (VENTAS)
    # ==========================================
    def crear_pantalla_dispensacion(self):
        frame = ctk.CTkFrame(self.contenedor_principal, fg_color="transparent")
        frame.grid_columnconfigure(0, weight=3)
        frame.grid_columnconfigure(1, weight=1)
        frame.grid_rowconfigure(0, weight=1)

        frame_izq = ctk.CTkFrame(frame, fg_color=self.color_panel, corner_radius=15)
        frame_izq.grid(row=0, column=0, sticky="nsew", padx=(0, 10))

        frame_busqueda = ctk.CTkFrame(frame_izq, fg_color="transparent")
        frame_busqueda.pack(fill="x", padx=20, pady=(20, 5))
        self.ent_buscador_venta = ctk.CTkEntry(frame_busqueda, placeholder_text="🔍 Buscar medicamento...",
                                               height=40, fg_color="#121212", border_width=0, corner_radius=8)
        self.ent_buscador_venta.pack(fill="x")
        self.ent_buscador_venta.bind("<KeyRelease>", self.buscar_productos_venta)

        self.frame_sugerencias_venta = ctk.CTkScrollableFrame(frame_izq, fg_color="#121212", corner_radius=8, height=0)
        self.frame_sugerencias_venta.pack(fill="x", padx=20, pady=(0, 5))

        encabezados = ctk.CTkFrame(frame_izq, fg_color="transparent")
        encabezados.pack(fill="x", padx=20, pady=(5, 10))
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

        frame_der = ctk.CTkFrame(frame, fg_color=self.color_panel, corner_radius=15)
        frame_der.grid(row=0, column=1, sticky="nsew")

        ctk.CTkLabel(frame_der, text="Método de Registro", font=("Helvetica", 14, "bold")).pack(anchor="w", padx=20, pady=(20, 5))

        self.combo_metodo_pago = ctk.CTkComboBox(frame_der, values=["Efectivo", "Tarjeta", "Billetera digital"], height=45,
                                                 fg_color="#121212", border_color=self.color_acento_verde,
                                                 button_color=self.color_acento_verde, button_hover_color="#059669",
                                                 dropdown_fg_color="#1E1E1E", font=("Helvetica", 13),
                                                 command=self.cambiar_metodo_pago)
        self.combo_metodo_pago.set("Efectivo")
        self.combo_metodo_pago.pack(fill="x", padx=20, pady=(0, 10))

        self.frame_efectivo = ctk.CTkFrame(frame_der, fg_color="transparent")
        self.frame_efectivo.pack(fill="x")

        ctk.CTkLabel(self.frame_efectivo, text="Efectivo Recibido", font=("Helvetica", 11), text_color=self.color_texto_secundario).pack(anchor="w", padx=20, pady=(10, 0))
        self.ent_efectivo = ctk.CTkEntry(self.frame_efectivo, fg_color="#121212", border_width=0, height=40, placeholder_text="S/ 0.00")
        self.ent_efectivo.pack(fill="x", padx=20, pady=5)
        self.ent_efectivo.bind("<KeyRelease>", self.calcular_vuelto)

        ctk.CTkLabel(self.frame_efectivo, text="Vuelto", font=("Helvetica", 11), text_color=self.color_texto_secundario).pack(anchor="w", padx=20, pady=(10, 0))
        self.lbl_vuelto = ctk.CTkLabel(self.frame_efectivo, text="S/ 0.00", font=("Helvetica", 16, "bold"),
                                       text_color=self.color_acento_verde, fg_color="#121212", height=40, corner_radius=8, anchor="w")
        self.lbl_vuelto.pack(fill="x", padx=20, pady=5)

        frame_acciones = ctk.CTkFrame(frame_der, fg_color="transparent")
        frame_acciones.pack(fill="x", side="bottom", padx=20, pady=20)
        ctk.CTkButton(frame_acciones, text="CANCELAR", fg_color="#121212", border_width=1, border_color="#3F3F46",
                      hover_color="#2A2A2A", height=45, width=90, command=self.limpiar_carrito).pack(side="left")
        ctk.CTkButton(frame_acciones, text="COMPLETAR\nREGISTRO →", fg_color=self.color_acento_verde,
                      hover_color="#059669", height=45, font=("Helvetica", 12, "bold"),
                      command=self.completar_registro).pack(side="right", fill="x", expand=True, padx=(10, 0))

        return frame

    def cambiar_metodo_pago(self, seleccion):
        self.metodo_pago = seleccion
        if seleccion == "Efectivo":
            self.frame_efectivo.pack(fill="x")
        else:
            self.frame_efectivo.pack_forget()

    # ==========================================
    # LÓGICA DE VENTAS Y CARRITO
    # ==========================================
    def buscar_productos_venta(self, event=None):
        texto = self.ent_buscador_venta.get().strip()
        for widget in self.frame_sugerencias_venta.winfo_children():
            widget.destroy()

        if not texto:
            self.frame_sugerencias_venta.configure(height=0)
            return

        conexion = sqlite3.connect(DB_NAME)
        cursor = conexion.cursor()
        cursor.execute("SELECT id, nombre, precio, stock_actual FROM productos WHERE nombre LIKE ? ORDER BY nombre LIMIT 6", (f"%{texto}%",))
        resultados = cursor.fetchall()
        conexion.close()

        if not resultados:
            self.frame_sugerencias_venta.configure(height=0)
            return

        self.frame_sugerencias_venta.configure(height=min(len(resultados), 6) * 38)

        for id_p, nombre, precio, stock in resultados:
            sin_stock = stock <= 0
            fila = ctk.CTkFrame(self.frame_sugerencias_venta, fg_color="transparent", cursor="hand2")
            fila.pack(fill="x", pady=2)
            lbl = ctk.CTkLabel(fila, text=f"[{id_p}] {nombre}  —  S/ {precio:.2f}  ({stock} uds)",
                               font=("Helvetica", 12), text_color=self.color_alerta if sin_stock else "white", anchor="w")
            lbl.pack(fill="x", padx=8, pady=3)
            if not sin_stock:
                for w in (fila, lbl):
                    w.bind("<Button-1>", lambda e, pid=id_p: self.seleccionar_producto_venta(pid))

    def seleccionar_producto_venta(self, id_prod):
        conexion = sqlite3.connect(DB_NAME)
        cursor = conexion.cursor()
        cursor.execute("SELECT nombre, precio, stock_actual FROM productos WHERE id = ?", (id_prod,))
        prod = cursor.fetchone()
        conexion.close()

        if not prod or prod[2] <= 0:
            return

        if id_prod in self.carrito:
            if self.carrito[id_prod]['cantidad'] < prod[2]:
                self.carrito[id_prod]['cantidad'] += 1
        else:
            self.carrito[id_prod] = {'nombre': prod[0], 'precio': prod[1], 'cantidad': 1, 'stock_max': prod[2]}

        self.ent_buscador_venta.delete(0, 'end')
        for widget in self.frame_sugerencias_venta.winfo_children():
            widget.destroy()
        self.frame_sugerencias_venta.configure(height=0)
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

    def eliminar_del_carrito(self, id_prod):
        if id_prod in self.carrito:
            del self.carrito[id_prod]
        self.actualizar_carrito_ui()

    def actualizar_carrito_ui(self):
        for widget in self.lista_carrito_ui.winfo_children():
            widget.destroy()

        self.total_actual = 0.0

        for id_prod, datos in self.carrito.items():
            subtotal = datos['cantidad'] * datos['precio']
            self.total_actual += subtotal

            item = ctk.CTkFrame(self.lista_carrito_ui, fg_color="transparent")
            item.pack(fill="x", pady=5)
            ctk.CTkFrame(item, height=1, fg_color="#2A2A2A").pack(fill="x", side="top", pady=(0, 10))

            info_frame = ctk.CTkFrame(item, fg_color="transparent")
            info_frame.pack(fill="x")

            ctk.CTkButton(info_frame, text="🗑", width=30, height=28, fg_color="#3B1010", hover_color="#7F1D1D",
                          text_color="#F87171", corner_radius=6, font=("Helvetica", 13),
                          command=lambda i=id_prod: self.eliminar_del_carrito(i)).pack(side="left", padx=(10, 6))

            ctk.CTkLabel(info_frame, text=datos['nombre'], font=("Helvetica", 14, "bold")).pack(side="left", padx=(0, 10))
            ctk.CTkLabel(info_frame, text=f"S/ {subtotal:.2f}", font=("Helvetica", 14)).pack(side="right", padx=(20, 10))

            cant_frame = ctk.CTkFrame(info_frame, fg_color="#121212", corner_radius=5)
            cant_frame.pack(side="right", padx=20)
            ctk.CTkButton(cant_frame, text="-", width=25, height=25, fg_color="transparent",
                          command=lambda i=id_prod: self.modificar_cantidad(i, "resta")).pack(side="left", padx=2)
            ctk.CTkLabel(cant_frame, text=str(datos['cantidad']), font=("Helvetica", 14, "bold"), width=30).pack(side="left")
            ctk.CTkButton(cant_frame, text="+", width=25, height=25, fg_color="transparent",
                          command=lambda i=id_prod: self.modificar_cantidad(i, "suma")).pack(side="left", padx=2)

        self.lbl_total.configure(text=f"Monto total S/ {self.total_actual:.2f}")
        self.calcular_vuelto()

    def calcular_vuelto(self, event=None):
        if self.metodo_pago != "Efectivo":
            return
        try:
            vuelto = float(self.ent_efectivo.get()) - self.total_actual
            if vuelto >= 0:
                self.lbl_vuelto.configure(text=f"S/ {vuelto:.2f}", text_color=self.color_acento_verde)
            else:
                self.lbl_vuelto.configure(text="Falta dinero", text_color=self.color_alerta)
        except ValueError:
            self.lbl_vuelto.configure(text="S/ 0.00", text_color=self.color_acento_verde)

    def limpiar_carrito(self):
        self.carrito = {}
        self.ent_efectivo.delete(0, 'end')
        self.combo_metodo_pago.set("Efectivo")
        self.metodo_pago = "Efectivo"
        self.frame_efectivo.pack(fill="x")
        self.actualizar_carrito_ui()

    def completar_registro(self):
        if not self.carrito:
            return

        ahora = datetime.now()
        # Copia inmutable de la venta ANTES de limpiar el carrito
        items = [{"nombre": d["nombre"], "precio": d["precio"], "cantidad": d["cantidad"],
                  "subtotal": d["cantidad"] * d["precio"]} for d in self.carrito.values()]
        total = sum(it["subtotal"] for it in items)
        venta = {
            "items": items,
            "total": total,
            "gravada": round(total / 1.18, 2),
            "igv": round(total - (total / 1.18), 2),
            "metodo_pago": self.metodo_pago,
            "fecha_legible": ahora.strftime("%d/%m/%Y %H:%M:%S"),
            "cliente_num": None,
        }

        conexion = sqlite3.connect(DB_NAME)
        cursor = conexion.cursor()
        alertas = []
        fecha_iso = ahora.isoformat(timespec="seconds")

        for id_prod, datos in self.carrito.items():
            cant = datos['cantidad']
            cursor.execute("UPDATE productos SET stock_actual = MAX(stock_actual - ?, 0), ventas_hoy = ventas_hoy + ? WHERE id = ?", (cant, cant, id_prod))
            cursor.execute(
                "INSERT INTO ventas_log (producto_id, nombre, cantidad, precio_unit, subtotal, metodo_pago, fecha_hora) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (id_prod, datos['nombre'], cant, datos['precio'], cant * datos['precio'], self.metodo_pago, fecha_iso))
            cursor.execute("SELECT nombre, stock_actual, stock_minimo FROM productos WHERE id = ?", (id_prod,))
            p = cursor.fetchone()
            if p[1] <= p[2]:
                alertas.append(p)

        conexion.commit()
        conexion.close()
        self.limpiar_carrito()

        # 1) Alerta de stock: automática, sin preguntar nada al usuario
        self.enviar_alerta_stock(alertas)

        # 2) Diálogo de comprobante (reemplaza al antiguo messagebox)
        VentanaComprobante(self, venta)

    # ==========================================
    # PANTALLA 3: ANÁLISIS DE DATOS (Gemma offline)
    # ==========================================
    def crear_pantalla_analisis(self):
        frame = ctk.CTkFrame(self.contenedor_principal, fg_color="transparent")
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(1, weight=1)

        fila_kpi = ctk.CTkFrame(frame, fg_color="transparent")
        fila_kpi.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        for i in range(4):
            fila_kpi.grid_columnconfigure(i, weight=1)

        self.kpi_ingresos = self._crear_tarjeta_kpi(fila_kpi, "INGRESOS HOY", "S/ 0.00", 0)
        self.kpi_unidades = self._crear_tarjeta_kpi(fila_kpi, "UNIDADES VENDIDAS", "0", 1)
        self.kpi_ticket = self._crear_tarjeta_kpi(fila_kpi, "TICKET PROMEDIO", "S/ 0.00", 2)
        self.kpi_inventario = self._crear_tarjeta_kpi(fila_kpi, "VALOR DE INVENTARIO", "S/ 0.00", 3)

        cuerpo = ctk.CTkFrame(frame, fg_color="transparent")
        cuerpo.grid(row=1, column=0, sticky="nsew")
        cuerpo.grid_columnconfigure(0, weight=3)
        cuerpo.grid_columnconfigure(1, weight=2)
        cuerpo.grid_rowconfigure(0, weight=1)

        panel_izq = ctk.CTkFrame(cuerpo, fg_color=self.color_panel, corner_radius=15)
        panel_izq.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        ctk.CTkLabel(panel_izq, text="DESGLOSE DE VENTAS DE HOY", font=("Helvetica", 14, "bold"), text_color="white").pack(anchor="w", padx=20, pady=(20, 5))
        encab = ctk.CTkFrame(panel_izq, fg_color="transparent")
        encab.pack(fill="x", padx=20, pady=(5, 5))
        ctk.CTkLabel(encab, text="PRODUCTO", font=("Helvetica", 11, "bold"), text_color=self.color_texto_secundario).pack(side="left")
        ctk.CTkLabel(encab, text="MÉTODO", font=("Helvetica", 11, "bold"), text_color=self.color_texto_secundario, width=110).pack(side="right", padx=10)
        ctk.CTkLabel(encab, text="INGRESO", font=("Helvetica", 11, "bold"), text_color=self.color_texto_secundario, width=90).pack(side="right", padx=10)
        self.frame_tabla_analisis = ctk.CTkScrollableFrame(panel_izq, fg_color="transparent")
        self.frame_tabla_analisis.pack(fill="both", expand=True, padx=15, pady=(0, 20))

        panel_der = ctk.CTkFrame(cuerpo, fg_color=self.color_panel, corner_radius=15)
        panel_der.grid(row=0, column=1, sticky="nsew")
        cab = ctk.CTkFrame(panel_der, fg_color="transparent")
        cab.pack(fill="x", padx=20, pady=(20, 10))
        ctk.CTkLabel(cab, text="🧠", font=("Helvetica", 16)).pack(side="left")
        ctk.CTkLabel(cab, text="INSIGHT DE GEMMA", font=("Helvetica", 13, "bold"), text_color=self.color_acento_verde).pack(side="left", padx=(8, 0))
        self.frame_insight_gemma = ctk.CTkScrollableFrame(panel_der, fg_color="transparent")
        self.frame_insight_gemma.pack(fill="both", expand=True, padx=15, pady=(0, 10))
        self.lbl_insight_gemma = ctk.CTkLabel(self.frame_insight_gemma, text="Generando análisis con Gemma...",
                                              font=("Helvetica", 13), text_color="white", justify="left", anchor="nw", wraplength=350)
        self.lbl_insight_gemma.pack(fill="both", expand=True, padx=5, pady=5)
        ctk.CTkButton(panel_der, text="🔄 Regenerar análisis con Gemma", fg_color="#121212", border_width=1,
                      border_color=self.color_acento_verde, text_color=self.color_acento_verde, hover_color="#2A2A2A",
                      command=self.actualizar_analisis).pack(fill="x", padx=20, pady=(0, 20))

        return frame

    def _crear_tarjeta_kpi(self, contenedor, titulo, valor_inicial, columna):
        tarjeta = ctk.CTkFrame(contenedor, fg_color=self.color_panel, corner_radius=12)
        tarjeta.grid(row=0, column=columna, sticky="ew", padx=6)
        ctk.CTkLabel(tarjeta, text=titulo, font=("Helvetica", 10, "bold"), text_color=self.color_texto_secundario).pack(anchor="w", padx=15, pady=(15, 2))
        lbl_valor = ctk.CTkLabel(tarjeta, text=valor_inicial, font=("Helvetica", 20, "bold"), text_color="white")
        lbl_valor.pack(anchor="w", padx=15, pady=(0, 15))
        return lbl_valor

    def actualizar_analisis(self):
        conexion = sqlite3.connect(DB_NAME)
        cursor = conexion.cursor()
        hoy = datetime.now().strftime("%Y-%m-%d")
        cursor.execute("SELECT nombre, cantidad, subtotal, metodo_pago FROM ventas_log WHERE fecha_hora LIKE ? ORDER BY fecha_hora DESC", (f"{hoy}%",))
        ventas_hoy = cursor.fetchall()
        cursor.execute("SELECT COALESCE(SUM(stock_actual * precio), 0) FROM productos")
        valor_inventario = cursor.fetchone()[0]
        cursor.execute("SELECT metodo_pago, COALESCE(SUM(subtotal),0), COUNT(*) FROM ventas_log WHERE fecha_hora LIKE ? GROUP BY metodo_pago", (f"{hoy}%",))
        por_metodo = cursor.fetchall()
        conexion.close()

        ingresos_hoy = sum(v[2] for v in ventas_hoy)
        unidades_hoy = sum(v[1] for v in ventas_hoy)
        num_tx = len(ventas_hoy)
        ticket_promedio = (ingresos_hoy / num_tx) if num_tx else 0.0

        self.kpi_ingresos.configure(text=f"S/ {ingresos_hoy:.2f}")
        self.kpi_unidades.configure(text=str(unidades_hoy))
        self.kpi_ticket.configure(text=f"S/ {ticket_promedio:.2f}")
        self.kpi_inventario.configure(text=f"S/ {valor_inventario:.2f}")

        for widget in self.frame_tabla_analisis.winfo_children():
            widget.destroy()
        if not ventas_hoy:
            ctk.CTkLabel(self.frame_tabla_analisis, text="Todavía no hay ventas registradas hoy", text_color=self.color_texto_secundario).pack(pady=20)
        else:
            for nombre, cantidad, subtotal, metodo in ventas_hoy:
                fila = ctk.CTkFrame(self.frame_tabla_analisis, fg_color="transparent")
                fila.pack(fill="x", pady=3)
                ctk.CTkLabel(fila, text=f"{nombre} x{cantidad}", font=("Helvetica", 12), text_color="white").pack(side="left")
                ctk.CTkLabel(fila, text=metodo, font=("Helvetica", 11), text_color=self.color_texto_secundario, width=110).pack(side="right", padx=10)
                ctk.CTkLabel(fila, text=f"S/ {subtotal:.2f}", font=("Helvetica", 12, "bold"), text_color=self.color_acento_verde, width=90).pack(side="right", padx=10)

        for w in self.frame_insight_gemma.winfo_children():
            w.destroy()
        self.lbl_insight_gemma = ctk.CTkLabel(self.frame_insight_gemma, text="Generando análisis con Gemma (offline)...",
                                              font=("Helvetica", 13), text_color="white", justify="left", anchor="nw", wraplength=350)
        self.lbl_insight_gemma.pack(fill="both", expand=True, padx=5, pady=5)
        datos = {"ingresos_hoy": ingresos_hoy, "unidades_hoy": unidades_hoy, "ticket_promedio": ticket_promedio,
                 "valor_inventario": valor_inventario, "por_metodo": por_metodo}
        threading.Thread(target=self._worker_insight_analisis, args=(datos,), daemon=True).start()

    def _worker_insight_analisis(self, datos):
        metodo_txt = ", ".join(f"{m}: S/ {t:.2f} ({c} ventas)" for m, t, c in datos["por_metodo"]) or "sin transacciones registradas"
        prompt = (
            "Eres un analista de datos para una posta médica rural en Perú que opera sin internet. "
            "Con las siguientes métricas REALES de hoy (no inventes ninguna cifra adicional), redacta un "
            "análisis breve de 4 a 5 líneas en español, con 1-2 recomendaciones operativas concretas.\n\n"
            f"Ingresos de hoy: S/ {datos['ingresos_hoy']:.2f}\n"
            f"Unidades vendidas: {datos['unidades_hoy']}\n"
            f"Ticket promedio: S/ {datos['ticket_promedio']:.2f}\n"
            f"Valor total del inventario actual: S/ {datos['valor_inventario']:.2f}\n"
            f"Desglose por método de pago: {metodo_txt}\n")
        resultado = llamar_gemma(prompt)
        if resultado is None:
            self.after(0, lambda: self._mostrar_insight_offline(datos))
        else:
            self.after(0, lambda: self._mostrar_insight_texto(resultado))

    def _mostrar_insight_texto(self, texto):
        """Muestra la respuesta de Gemma como texto en el panel de insight."""
        for w in self.frame_insight_gemma.winfo_children():
            w.destroy()
        self.lbl_insight_gemma = ctk.CTkLabel(self.frame_insight_gemma, text=texto,
                                              font=("Helvetica", 13), text_color="white",
                                              justify="left", anchor="nw", wraplength=350)
        self.lbl_insight_gemma.pack(fill="both", expand=True, padx=5, pady=5)

    def _mostrar_insight_offline(self, datos):
        """Construye tarjetas de métricas estructuradas cuando Gemma no está disponible."""
        for w in self.frame_insight_gemma.winfo_children():
            w.destroy()

        COLOR_TARJETA = "#1E1E1E"
        COLOR_ACENTO  = self.color_acento_verde
        COLOR_SEC     = self.color_texto_secundario

        # --- Título ---
        titulo_frame = ctk.CTkFrame(self.frame_insight_gemma, fg_color="transparent")
        titulo_frame.pack(fill="x", pady=(4, 10))
        ctk.CTkLabel(titulo_frame, text="📊", font=("Helvetica", 18)).pack(side="left")
        ctk.CTkLabel(titulo_frame, text=" Resumen de ventas del día",
                     font=("Helvetica", 14, "bold"), text_color="white").pack(side="left")

        # --- Tarjetas de métricas principales ---
        metricas = [
            ("💰", "Ingresos totales",    f"S/ {datos['ingresos_hoy']:.2f}"),
            ("📦", "Unidades vendidas",   str(datos['unidades_hoy'])),
            ("🧾", "Ticket promedio",     f"S/ {datos['ticket_promedio']:.2f}"),
        ]
        for icono, etiqueta, valor in metricas:
            tarjeta = ctk.CTkFrame(self.frame_insight_gemma, fg_color=COLOR_TARJETA, corner_radius=10)
            tarjeta.pack(fill="x", pady=4, padx=2)
            ctk.CTkLabel(tarjeta, text=icono, font=("Helvetica", 20)).pack(side="left", padx=(12, 8), pady=10)
            col = ctk.CTkFrame(tarjeta, fg_color="transparent")
            col.pack(side="left", fill="both", expand=True, pady=8)
            ctk.CTkLabel(col, text=etiqueta, font=("Helvetica", 10, "bold"),
                         text_color=COLOR_SEC, anchor="w").pack(anchor="w")
            ctk.CTkLabel(col, text=valor, font=("Helvetica", 16, "bold"),
                         text_color="white", anchor="w").pack(anchor="w")

        # --- Separador Métodos de pago ---
        sep = ctk.CTkFrame(self.frame_insight_gemma, fg_color="#2A2A2A", height=1)
        sep.pack(fill="x", pady=(8, 6), padx=2)
        ctk.CTkLabel(self.frame_insight_gemma, text="💳  Métodos de pago",
                     font=("Helvetica", 11, "bold"), text_color=COLOR_SEC, anchor="w").pack(anchor="w", padx=4)

        for metodo, total, cantidad in datos["por_metodo"]:
            fila = ctk.CTkFrame(self.frame_insight_gemma, fg_color=COLOR_TARJETA, corner_radius=8)
            fila.pack(fill="x", pady=3, padx=2)
            ctk.CTkLabel(fila, text=metodo, font=("Helvetica", 12), text_color="white").pack(side="left", padx=12, pady=7)
            ctk.CTkLabel(fila, text=f"{cantidad} venta{'s' if cantidad != 1 else ''}",
                         font=("Helvetica", 11), text_color=COLOR_SEC).pack(side="right", padx=10)
            ctk.CTkLabel(fila, text=f"S/ {total:.2f}", font=("Helvetica", 12, "bold"),
                         text_color=COLOR_ACENTO).pack(side="right", padx=4)


if __name__ == "__main__":
    inicializar_bd()
    app = ChasquiLogApp()
    app.mainloop()