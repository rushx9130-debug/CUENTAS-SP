import os, re, logging, secrets, string
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)
import sqlite3

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "TU_TOKEN_AQUI")
ADMIN_ID  = int(os.getenv("ADMIN_ID", "0"))
DB_PATH   = os.getenv("DB_PATH", "bot.db")
PASSWORD  = "Gowther2026"

TIPOS = {
    "individual_30": {"nombre": "INDIVIDUAL", "dias": 30,  "emoji": "👤"},
    "individual_90": {"nombre": "INDIVIDUAL", "dias": 90,  "emoji": "👤"},
    "familiar_30":   {"nombre": "FAMILIAR",   "dias": 30,  "emoji": "👨‍👩‍👧‍👦"},
    "dual_30":       {"nombre": "DUAL",       "dias": 30,  "emoji": "👥"},
    "dual_90":       {"nombre": "DUAL",       "dias": 90,  "emoji": "👥"},
}

# ─── DB ───────────────────────────────────────────────────────────────────────
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS usuarios (
            user_id   INTEGER PRIMARY KEY,
            username  TEXT,
            nombre    TEXT,
            creditos  REAL DEFAULT 0,
            bloqueado INTEGER DEFAULT 0,
            creado_en TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS cuentas (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            tipo          TEXT,
            correo        TEXT,
            contrasena    TEXT DEFAULT 'Gowther2026',
            nombre_user   TEXT,
            entregado     INTEGER DEFAULT 0,
            entregado_a   INTEGER,
            fecha_entrega TEXT,
            fecha_fin     TEXT,
            creado_en     TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS precios (
            tipo   TEXT PRIMARY KEY,
            precio REAL
        );
        CREATE TABLE IF NOT EXISTS llaves (
            llave     TEXT PRIMARY KEY,
            creditos  REAL,
            nota      TEXT,
            usada     INTEGER DEFAULT 0,
            usada_por INTEGER,
            usada_en  TEXT
        );
        CREATE TABLE IF NOT EXISTS transacciones (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            tipo    TEXT,
            monto   REAL,
            desc    TEXT,
            fecha   TEXT DEFAULT (datetime('now'))
        );
        """)

        # Migración tabla precios: si tiene columnas extra (versión vieja), recrear
        cols_p = c.execute("PRAGMA table_info(precios)").fetchall()
        if len(cols_p) != 2:
            logger.info("🔧 Migrando tabla precios...")
            filas = []
            try:
                filas = c.execute("SELECT tipo, precio FROM precios").fetchall()
            except Exception:
                pass
            c.execute("DROP TABLE IF EXISTS precios")
            c.execute("CREATE TABLE precios (tipo TEXT PRIMARY KEY, precio REAL)")
            for f in filas:
                c.execute("INSERT OR IGNORE INTO precios(tipo,precio) VALUES(?,?)", (f[0], f[1]))

        # Migración tabla cuentas: renombrar email → correo si la DB vieja usa email
        cols_c = [r[1] for r in c.execute("PRAGMA table_info(cuentas)").fetchall()]
        if "email" in cols_c and "correo" not in cols_c:
            logger.info("🔧 Migrando columna email → correo en tabla cuentas...")
            # SQLite no soporta RENAME COLUMN antes de 3.25; reconstruimos la tabla
            c.executescript("""
                CREATE TABLE IF NOT EXISTS cuentas_new (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    tipo          TEXT,
                    correo        TEXT,
                    contrasena    TEXT DEFAULT 'Gowther2026',
                    nombre_user   TEXT,
                    entregado     INTEGER DEFAULT 0,
                    entregado_a   INTEGER,
                    fecha_entrega TEXT,
                    fecha_fin     TEXT,
                    creado_en     TEXT DEFAULT (datetime('now'))
                );
                INSERT INTO cuentas_new(id, tipo, correo, contrasena, nombre_user,
                                        entregado, entregado_a, fecha_entrega, fecha_fin, creado_en)
                SELECT id, tipo, email, contrasena, nombre_user,
                       entregado, entregado_a, fecha_entrega, fecha_fin, creado_en
                FROM cuentas;
                DROP TABLE cuentas;
                ALTER TABLE cuentas_new RENAME TO cuentas;
            """)
            cols_c = [r[1] for r in c.execute("PRAGMA table_info(cuentas)").fetchall()]
            logger.info("✅ Columna renombrada: email → correo")

        # Eliminar filas con dominio gowtherxax.com (datos basura)
        deleted = c.execute(
            "DELETE FROM cuentas WHERE correo LIKE '%gowtherxax.com%'"
        ).rowcount
        if deleted:
            logger.info(f"🗑️ Eliminadas {deleted} cuenta(s) con dominio gowtherxax.com")

        # Migración tabla cuentas: agregar columnas faltantes si no existen
        cols_c = [r[1] for r in c.execute("PRAGMA table_info(cuentas)").fetchall()]
        migraciones_cuentas = [
            ("nombre_user",   "TEXT",    ""),
            ("entregado",     "INTEGER", "0"),
            ("entregado_a",   "INTEGER", ""),
            ("fecha_entrega", "TEXT",    ""),
            ("fecha_fin",     "TEXT",    ""),
        ]
        for col, tipo_col, default in migraciones_cuentas:
            if col not in cols_c:
                default_sql = f" DEFAULT {default}" if default != "" else ""
                c.execute(f"ALTER TABLE cuentas ADD COLUMN {col} {tipo_col}{default_sql}")
                logger.info(f"🔧 Columna agregada a cuentas: {col}")

        # Insertar precios por defecto si no existen
        defaults = [
            ("individual_30", 1.25),
            ("individual_90", 3.00),
            ("familiar_30",   2.00),
            ("dual_30",       1.50),
            ("dual_90",       3.50),
        ]
        for tipo, precio in defaults:
            c.execute("INSERT OR IGNORE INTO precios(tipo,precio) VALUES(?,?)", (tipo, precio))

    logger.info(f"✅ DB lista: {DB_PATH}")

# ─── Helpers ──────────────────────────────────────────────────────────────────
def get_user(uid):
    with get_conn() as c:
        return c.execute("SELECT * FROM usuarios WHERE user_id=?", (uid,)).fetchone()

def ensure_user(uid, username, nombre):
    with get_conn() as c:
        c.execute("INSERT OR IGNORE INTO usuarios(user_id,username,nombre) VALUES(?,?,?)",
                  (uid, username or "", nombre or ""))

def get_precio(tipo):
    with get_conn() as c:
        r = c.execute("SELECT precio FROM precios WHERE tipo=?", (tipo,)).fetchone()
        return r["precio"] if r else None

def stock_libre(tipo):
    with get_conn() as c:
        r = c.execute("SELECT COUNT(*) AS n FROM cuentas WHERE tipo=? AND entregado=0", (tipo,)).fetchone()
        return r["n"]

def fmt_precio(p):
    return f"${p:.2f}"

def fecha_fin_str(dias):
    return (datetime.now() + timedelta(days=dias)).strftime("%d/%m/%Y")


def normalizar_linea_cuenta(linea: str) -> str:
    linea = linea.strip()
    m = re.search(r'mailto:([^\)\]\s]+)', linea, flags=re.I)
    if m:
        correo = m.group(1)
        resto = re.sub(r'\[[^\]]*\]\(mailto:[^\)]+\)', correo, linea, flags=re.I)
        linea = resto.strip()
    linea = linea.replace('<', ' ').replace('>', ' ').replace('(', ' ').replace(')', ' ').replace('[', ' ').replace(']', ' ')
    linea = re.sub(r'\s+', ' ', linea).strip()
    return linea

def parsear_y_guardar_cuentas(texto):
    tipo_actual = None
    insertadas = 0
    errores = 0
    tipo_map = {
        "individual": "individual_90",
        "familiar": "familiar_30",
        "dual": "dual_90",
        "individual30": "individual_30",
        "individual90": "individual_90",
        "familiar30": "familiar_30",
        "dual30": "dual_30",
        "dual90": "dual_90",
    }

    for raw in texto.splitlines():
        linea = raw.strip()
        if not linea:
            continue

        linea_lower = linea.lower().strip()
        if linea_lower in TIPOS:
            tipo_actual = linea_lower
            continue
        if linea_lower in tipo_map:
            tipo_actual = tipo_map[linea_lower]
            continue

        linea = normalizar_linea_cuenta(linea)
        if not tipo_actual:
            continue
        if '@' not in linea:
            continue

        parts = linea.split()
        if len(parts) < 2:
            logger.error(f"Linea inválida sin nombre_user: {raw}")
            errores += 1
            continue

        correo = parts[0].strip()
        nombre_u = parts[1].strip()
        passw = parts[2].strip() if len(parts) > 2 else PASSWORD

        if not re.match(r'^[^\s@]+@[^\s@]+\.[^\s@]+$', correo):
            logger.error(f"Correo inválido: {correo} | linea={raw}")
            errores += 1
            continue

        try:
            with get_conn() as c:
                cols = {r[1]: r[2].upper() for r in c.execute("PRAGMA table_info(cuentas)").fetchall()}
                migraciones = [
                    ("tipo", "TEXT", None),
                    ("correo", "TEXT", None),
                    ("contrasena", "TEXT", f"'{PASSWORD}'"),
                    ("nombre_user", "TEXT", None),
                    ("entregado", "INTEGER", "0"),
                    ("entregado_a", "INTEGER", None),
                    ("fecha_entrega", "TEXT", None),
                    ("fecha_fin", "TEXT", None),
                ]
                for col, tipo_col, default in migraciones:
                    if col not in cols:
                        default_sql = f" DEFAULT {default}" if default is not None else ""
                        c.execute(f"ALTER TABLE cuentas ADD COLUMN {col} {tipo_col}{default_sql}")
                cols = {r[1]: r[2].upper() for r in c.execute("PRAGMA table_info(cuentas)").fetchall()}

                # Compatibilidad con esquemas viejos: algunas DB usan email en vez de correo
                campo_correo = "correo" if "correo" in cols else ("email" if "email" in cols else None)
                if not campo_correo:
                    c.execute("ALTER TABLE cuentas ADD COLUMN correo TEXT")
                    campo_correo = "correo"

                insert_sql = f"INSERT INTO cuentas(tipo,{campo_correo},contrasena,nombre_user) VALUES(?,?,?,?)"
                c.execute(insert_sql, (tipo_actual, correo, passw, nombre_u))
            insertadas += 1
        except Exception as e:
            logger.error(f"Error insertando cuenta | linea={raw} | normalizada={linea} | error={e}")
            errores += 1



    if insertadas == 0 and errores == 0:
        return "⚠️ No se encontraron cuentas válidas."
    return f"✅ {insertadas} cuenta(s) agregada(s)" + (f"\n⚠️ {errores} error(es)" if errores else "")


# ═══════════════════════════════════════════════════════════════════════════════
# TECLADOS
# ═══════════════════════════════════════════════════════════════════════════════
def kb_usuario():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Mis créditos",    callback_data="mis_creditos"),
         InlineKeyboardButton("🏪 Precios & stock",  callback_data="ver_precios")],
        [InlineKeyboardButton("📦 Pedir cuenta",     callback_data="pedir_menu"),
         InlineKeyboardButton("📋 Mis cuentas",      callback_data="mis_cuentas")],
        [InlineKeyboardButton("🔑 Canjear llave",    callback_data="canjear_prompt")],
    ])

def kb_admin():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔑 Generar llave",   callback_data="adm_genkey"),
         InlineKeyboardButton("📋 Ver llaves",       callback_data="adm_llaves")],
        [InlineKeyboardButton("➕ Agregar cuentas",  callback_data="adm_addcuenta"),
         InlineKeyboardButton("📦 Stock",            callback_data="adm_stock")],
        [InlineKeyboardButton("💵 Precios",          callback_data="adm_precios"),
         InlineKeyboardButton("👥 Usuarios",         callback_data="adm_usuarios")],
        [InlineKeyboardButton("🔍 Buscar usuario",   callback_data="adm_buscar"),
         InlineKeyboardButton("📊 Estadísticas",     callback_data="adm_stats")],
        [InlineKeyboardButton("💬 Broadcast",        callback_data="adm_broadcast"),
         InlineKeyboardButton("💰 Dar créditos",     callback_data="adm_creditos")],
        [InlineKeyboardButton("🚫 Bloquear usuario", callback_data="adm_bloquear")],
    ])

def kb_volver_usuario():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Volver", callback_data="menu_principal")]])

def kb_volver_admin():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Volver", callback_data="adm_back")]])

# ═══════════════════════════════════════════════════════════════════════════════
# COMANDOS PRINCIPALES
# ═══════════════════════════════════════════════════════════════════════════════
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    ensure_user(u.id, u.username, u.full_name)
    await update.message.reply_text(
        f"👋 Hola *{u.first_name}*!\n\nBienvenido al bot de cuentas. Usa los botones para navegar.",
        parse_mode="Markdown",
        reply_markup=kb_usuario()
    )

async def cmd_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ No autorizado.")
        return
    await update.message.reply_text(
        "🛡️ *Panel de Administración*\n\nElige una acción:",
        parse_mode="Markdown",
        reply_markup=kb_admin()
    )

# ═══════════════════════════════════════════════════════════════════════════════
# CALLBACKS — USUARIO
# ═══════════════════════════════════════════════════════════════════════════════
async def cb_mis_creditos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    u = get_user(q.from_user.id)
    creditos = u["creditos"] if u else 0
    await q.edit_message_text(
        f"💰 *Tus créditos:* `{fmt_precio(creditos)}`",
        parse_mode="Markdown",
        reply_markup=kb_volver_usuario()
    )

async def cb_ver_precios(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    with get_conn() as c:
        precios = c.execute("SELECT tipo, precio FROM precios ORDER BY tipo").fetchall()
    lines = []
    for p in precios:
        t     = TIPOS.get(p["tipo"], {})
        stock = stock_libre(p["tipo"])
        lines.append(
            f"{t.get('emoji','📦')} *{t.get('nombre','?')} {t.get('dias','')} días*\n"
            f"   💵 `{fmt_precio(p['precio'])}` | 📦 Stock: `{stock}`"
        )
    texto = "🏪 *Precios y Stock*\n\n" + "\n\n".join(lines) if lines else "Sin precios configurados."
    await q.edit_message_text(texto, parse_mode="Markdown", reply_markup=kb_volver_usuario())

async def cb_pedir_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    btns = []
    for tipo, info in TIPOS.items():
        precio = get_precio(tipo)
        stock  = stock_libre(tipo)
        label  = f"{info['emoji']} {info['nombre']} {info['dias']}d — {fmt_precio(precio)} ({stock} disp.)"
        btns.append([InlineKeyboardButton(label, callback_data=f"pedir_{tipo}")])
    btns.append([InlineKeyboardButton("⬅️ Volver", callback_data="menu_principal")])
    await q.edit_message_text(
        "📦 *¿Qué tipo de cuenta quieres?*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(btns)
    )

async def cb_pedir_tipo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query; await q.answer()
    tipo = q.data.replace("pedir_", "")
    uid  = q.from_user.id
    u    = get_user(uid)

    if not u:
        await q.answer("❌ Usuario no encontrado.", show_alert=True); return
    if u["bloqueado"]:
        await q.answer("🚫 Estás bloqueado.", show_alert=True); return

    precio = get_precio(tipo)
    if precio is None:
        await q.answer("❌ Tipo no configurado.", show_alert=True); return

    if u["creditos"] < precio:
        await q.edit_message_text(
            f"❌ *Créditos insuficientes*\n\n"
            f"Necesitas `{fmt_precio(precio)}` y tienes `{fmt_precio(u['creditos'])}`.",
            parse_mode="Markdown",
            reply_markup=kb_volver_usuario()
        ); return

    if stock_libre(tipo) == 0:
        await q.edit_message_text(
            "😔 *Sin stock disponible* para este tipo por ahora.",
            parse_mode="Markdown",
            reply_markup=kb_volver_usuario()
        ); return

    t  = TIPOS[tipo]
    ff = fecha_fin_str(t["dias"])

    with get_conn() as c:
        cuenta = c.execute(
            "SELECT * FROM cuentas WHERE tipo=? AND entregado=0 LIMIT 1", (tipo,)
        ).fetchone()
        if not cuenta:
            await q.answer("Stock agotado.", show_alert=True); return
        c.execute(
            "UPDATE cuentas SET entregado=1, entregado_a=?, fecha_entrega=datetime('now'), fecha_fin=? WHERE id=?",
            (uid, ff, cuenta["id"])
        )
        c.execute("UPDATE usuarios SET creditos=creditos-? WHERE user_id=?", (precio, uid))
        c.execute(
            "INSERT INTO transacciones(user_id,tipo,monto,desc) VALUES(?,?,?,?)",
            (uid, "compra", -precio, f"Cuenta {tipo}")
        )

    garantia     = (cuenta["nombre_user"] or "").strip()
    garantia_txt = f"👤 *Nombre de usuario para garantía*\n`{garantia}`\n\n" if garantia else ""

    msg = (
        f"✅ *¡Cuenta entregada!*\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 Cuenta *{t['nombre']} {t['dias']} días*\n\n"
        f"📅 Fin  `{ff}`\n\n"
        + garantia_txt +
        f"📧 Correo:\n`{(cuenta['correo'] if 'correo' in cuenta.keys() else cuenta['email'])}`\n\n"
        f"🔒 Contraseña\n`{cuenta['contrasena']}`\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )
    await q.edit_message_text(
        msg, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Menú", callback_data="menu_principal")]])
    )

async def cb_mis_cuentas(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q   = update.callback_query; await q.answer()
    uid = q.from_user.id
    with get_conn() as c:
        cuentas = c.execute(
            "SELECT * FROM cuentas WHERE entregado_a=? ORDER BY fecha_entrega DESC LIMIT 10", (uid,)
        ).fetchall()
    if not cuentas:
        await q.edit_message_text("📭 Aún no tienes cuentas.", reply_markup=kb_volver_usuario()); return

    lines = []
    for cu in cuentas:
        t = TIPOS.get(cu["tipo"], {})
        garantia_txt = f"👤 `{cu['nombre_user']}`\n" if cu["nombre_user"] else ""
        lines.append(
            f"{'—'*20}\n"
            f"{t.get('emoji','📦')} *{t.get('nombre','?')} {t.get('dias','')} días*\n"
            f"📅 Fin: `{cu['fecha_fin'] or 'N/A'}`\n"
            + garantia_txt +
            f"📧 `{cu['correo']}`\n"
            f"🔒 `{cu['contrasena']}`"
        )
    await q.edit_message_text(
        "📋 *Tus cuentas:*\n\n" + "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=kb_volver_usuario()
    )

async def cb_canjear_prompt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    ctx.user_data["esperando"] = "llave"
    await q.edit_message_text(
        "🔑 Envía tu llave de créditos:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancelar", callback_data="menu_principal")]])
    )

async def cb_menu_principal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    ctx.user_data.pop("esperando", None)
    ctx.user_data.pop("adm_esperando", None)
    await q.edit_message_text(
        f"🏠 *Menú principal*\n\nHola *{q.from_user.first_name}*, elige una opción:",
        parse_mode="Markdown",
        reply_markup=kb_usuario()
    )

# ─── Mensajes usuario (canje llave) ──────────────────────────────────────────
async def msg_usuario(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    text = update.message.text.strip()

    if ctx.user_data.get("esperando") == "llave":
        ctx.user_data.pop("esperando", None)
        with get_conn() as c:
            llave = c.execute("SELECT * FROM llaves WHERE llave=? AND usada=0", (text,)).fetchone()
            if not llave:
                await update.message.reply_text("❌ Llave inválida o ya usada.", reply_markup=kb_usuario()); return
            c.execute("UPDATE llaves SET usada=1, usada_por=?, usada_en=datetime('now') WHERE llave=?", (uid, text))
            c.execute("UPDATE usuarios SET creditos=creditos+? WHERE user_id=?", (llave["creditos"], uid))
            c.execute(
                "INSERT INTO transacciones(user_id,tipo,monto,desc) VALUES(?,?,?,?)",
                (uid, "recarga", llave["creditos"], f"Llave {text[:8]}...")
            )
        await update.message.reply_text(
            f"✅ *¡Créditos recargados!*\n\n💰 `+{fmt_precio(llave['creditos'])}` agregados.",
            parse_mode="Markdown",
            reply_markup=kb_usuario()
        )
        return

    await update.message.reply_text("Usa el menú 👇", reply_markup=kb_usuario())

# ═══════════════════════════════════════════════════════════════════════════════
# CALLBACKS — ADMIN
# ═══════════════════════════════════════════════════════════════════════════════
def es_admin(uid): return uid == ADMIN_ID

async def adm_back(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    ctx.user_data.pop("adm_esperando", None)
    await q.edit_message_text(
        "🛡️ *Panel de Administración*\n\nElige una acción:",
        parse_mode="Markdown", reply_markup=kb_admin()
    )

async def adm_genkey(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if not es_admin(q.from_user.id): return
    ctx.user_data["adm_esperando"] = "genkey"
    await q.edit_message_text(
        "🔑 *Generar llave*\n\nFormato: `<monto> [cantidad] [nota]`\n\n"
        "Ejemplos:\n`3.50`\n`1.25 5`\n`2.00 3 VIP`",
        parse_mode="Markdown", reply_markup=kb_volver_admin()
    )

async def adm_llaves(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if not es_admin(q.from_user.id): return
    with get_conn() as c:
        llaves = c.execute("SELECT * FROM llaves WHERE usada=0 ORDER BY rowid DESC LIMIT 20").fetchall()
    if not llaves:
        await q.edit_message_text("📭 No hay llaves disponibles.", reply_markup=kb_volver_admin()); return
    lines = [
        f"`{l['llave']}` — `{fmt_precio(l['creditos'])}`" + (f" — _{l['nota']}_" if l["nota"] else "")
        for l in llaves
    ]
    await q.edit_message_text(
        "🔑 *Llaves disponibles:*\n\n" + "\n".join(lines),
        parse_mode="Markdown", reply_markup=kb_volver_admin()
    )

async def adm_addcuenta(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if not es_admin(q.from_user.id): return
    ctx.user_data["adm_esperando"] = "addcuenta"
    texto_add = (
        "➕ *Agregar cuentas*\n\n"
        "Envía un archivo .txt o escribe directamente.\n\n"
        "Tipos válidos:\n"
        "individual30, individual90, familiar30, dual30, dual90\n\n"
        "Formato por línea:\n"
        "  correo@x.com nombre_usuario\n"
        "  correo@x.com nombre_usuario Contrasena\n\n"
        "Contraseña por defecto: Gowther2026"
    )
    await q.edit_message_text(texto_add, parse_mode="Markdown", reply_markup=kb_volver_admin())

async def adm_stock(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if not es_admin(q.from_user.id): return
    with get_conn() as c:
        rows = c.execute(
            "SELECT tipo, COUNT(*) AS total, SUM(entregado) AS vendidas FROM cuentas GROUP BY tipo"
        ).fetchall()
    if not rows:
        await q.edit_message_text("📭 Sin cuentas en inventario.", reply_markup=kb_volver_admin()); return
    lines = []
    for r in rows:
        t      = TIPOS.get(r["tipo"], {})
        libres = r["total"] - (r["vendidas"] or 0)
        lines.append(
            f"{t.get('emoji','📦')} *{t.get('nombre','?')} {t.get('dias','')}d* — "
            f"✅`{libres}` libre | 🏷️`{r['vendidas'] or 0}` vendida"
        )
    await q.edit_message_text(
        "📦 *Stock de cuentas:*\n\n" + "\n".join(lines),
        parse_mode="Markdown", reply_markup=kb_volver_admin()
    )

async def adm_precios(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if not es_admin(q.from_user.id): return
    with get_conn() as c:
        precios = c.execute("SELECT tipo, precio FROM precios ORDER BY tipo").fetchall()
    lines = []
    for p in precios:
        t = TIPOS.get(p["tipo"], {})
        lines.append(f"{t.get('emoji','📦')} *{t.get('nombre','?')} {t.get('dias','')}d* → `{fmt_precio(p['precio'])}`")
    ctx.user_data["adm_esperando"] = "setprecio"
    await q.edit_message_text(
        "💵 *Precios actuales:*\n\n" + "\n".join(lines) +
        "\n\n📝 Para cambiar envía: `<tipo> <precio>`\n"
        "Ej: `individual_90 3.75`",
        parse_mode="Markdown", reply_markup=kb_volver_admin()
    )

async def adm_usuarios(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if not es_admin(q.from_user.id): return
    with get_conn() as c:
        users = c.execute("SELECT * FROM usuarios ORDER BY creado_en DESC LIMIT 20").fetchall()
    if not users:
        await q.edit_message_text("📭 Sin usuarios.", reply_markup=kb_volver_admin()); return
    lines = []
    for u in users:
        estado = "🚫" if u["bloqueado"] else "✅"
        uname  = f"@{u['username']}" if u["username"] else u["nombre"] or "N/A"
        lines.append(f"{estado} `{u['user_id']}` {uname} — `{fmt_precio(u['creditos'])}`")
    await q.edit_message_text(
        "👥 *Últimos 20 usuarios:*\n\n" + "\n".join(lines),
        parse_mode="Markdown", reply_markup=kb_volver_admin()
    )

async def adm_buscar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if not es_admin(q.from_user.id): return
    ctx.user_data["adm_esperando"] = "buscar_usuario"
    await q.edit_message_text(
        "🔍 *Buscar usuario*\n\nEnvía el ID numérico o @username:",
        parse_mode="Markdown", reply_markup=kb_volver_admin()
    )

async def adm_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if not es_admin(q.from_user.id): return
    with get_conn() as c:
        total_users   = c.execute("SELECT COUNT(*) AS n FROM usuarios").fetchone()["n"]
        bloqueados    = c.execute("SELECT COUNT(*) AS n FROM usuarios WHERE bloqueado=1").fetchone()["n"]
        total_cuentas = c.execute("SELECT COUNT(*) AS n FROM cuentas").fetchone()["n"]
        vendidas      = c.execute("SELECT COUNT(*) AS n FROM cuentas WHERE entregado=1").fetchone()["n"]
        ingresos      = c.execute("SELECT COALESCE(SUM(monto),0) AS s FROM transacciones WHERE tipo='compra'").fetchone()["s"]
        llaves_gen    = c.execute("SELECT COUNT(*) AS n FROM llaves").fetchone()["n"]
        llaves_usadas = c.execute("SELECT COUNT(*) AS n FROM llaves WHERE usada=1").fetchone()["n"]
    await q.edit_message_text(
        f"📊 *Estadísticas del bot*\n\n"
        f"👥 Usuarios: `{total_users}` (🚫 bloqueados: `{bloqueados}`)\n"
        f"📦 Cuentas: `{total_cuentas}` (vendidas: `{vendidas}`)\n"
        f"🔑 Llaves: `{llaves_gen}` (usadas: `{llaves_usadas}`)\n"
        f"💵 Ingresos: `{fmt_precio(abs(ingresos))}`",
        parse_mode="Markdown", reply_markup=kb_volver_admin()
    )

async def adm_broadcast_btn(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if not es_admin(q.from_user.id): return
    ctx.user_data["adm_esperando"] = "broadcast"
    await q.edit_message_text(
        "📢 *Broadcast*\n\nEscribe el mensaje a enviar a todos los usuarios:",
        parse_mode="Markdown", reply_markup=kb_volver_admin()
    )

async def adm_creditos_btn(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if not es_admin(q.from_user.id): return
    ctx.user_data["adm_esperando"] = "dar_creditos"
    await q.edit_message_text(
        "💰 *Dar créditos*\n\nFormato: `<user_id> <monto>`\nEj: `123456789 5.50`",
        parse_mode="Markdown", reply_markup=kb_volver_admin()
    )

async def adm_bloquear_btn(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if not es_admin(q.from_user.id): return
    ctx.user_data["adm_esperando"] = "bloquear"
    await q.edit_message_text(
        "🚫 *Bloquear / Desbloquear*\n\nEnvía el user_id:",
        parse_mode="Markdown", reply_markup=kb_volver_admin()
    )

async def adm_toggle_user(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if not es_admin(q.from_user.id): return
    target = int(q.data.split("_")[-1])
    with get_conn() as c:
        u = c.execute("SELECT * FROM usuarios WHERE user_id=?", (target,)).fetchone()
        if not u:
            await q.answer("❌ No encontrado", show_alert=True); return
        nuevo = 0 if u["bloqueado"] else 1
        c.execute("UPDATE usuarios SET bloqueado=? WHERE user_id=?", (nuevo, target))
    estado = "🚫 Bloqueado" if nuevo else "✅ Desbloqueado"
    await q.answer(f"{estado}: {target}", show_alert=True)

# ─── Mensajes admin ───────────────────────────────────────────────────────────
async def msg_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    text = (update.message.text or "").strip()
    esp  = ctx.user_data.get("adm_esperando")
    if not esp:
        return

    # ── Generar llave ──────────────────────────────────────────────────────────
    if esp == "genkey":
        ctx.user_data.pop("adm_esperando")
        parts = text.split()
        try:
            monto    = float(parts[0])
            cantidad = int(parts[1]) if len(parts) > 1 else 1
            nota     = " ".join(parts[2:]) if len(parts) > 2 else ""
        except Exception:
            await update.message.reply_text("❌ Formato incorrecto.\nEj: `2.50 3 VIP`",
                parse_mode="Markdown", reply_markup=kb_admin()); return
        chars    = string.ascii_uppercase + string.digits
        generadas = []
        with get_conn() as c:
            for _ in range(cantidad):
                llave = "".join(secrets.choice(chars) for _ in range(16))
                c.execute("INSERT INTO llaves(llave,creditos,nota) VALUES(?,?,?)", (llave, monto, nota))
                generadas.append(llave)
        lines = "\n".join(f"`{l}`" for l in generadas)
        await update.message.reply_text(
            f"✅ *{cantidad}* llave(s) de `{fmt_precio(monto)}`:\n\n{lines}",
            parse_mode="Markdown", reply_markup=kb_admin())
        return

    # ── Cambiar precio ─────────────────────────────────────────────────────────
    if esp == "setprecio":
        ctx.user_data.pop("adm_esperando")
        parts = text.split()
        if len(parts) != 2 or parts[0] not in TIPOS:
            await update.message.reply_text("❌ Formato: `<tipo> <precio>`\nEj: `individual_90 3.75`",
                parse_mode="Markdown", reply_markup=kb_admin()); return
        try:
            nuevo = float(parts[1])
        except Exception:
            await update.message.reply_text("❌ Precio inválido.", reply_markup=kb_admin()); return
        with get_conn() as c:
            c.execute("INSERT OR REPLACE INTO precios(tipo,precio) VALUES(?,?)", (parts[0], nuevo))
        t = TIPOS[parts[0]]
        await update.message.reply_text(
            f"✅ {t['emoji']} *{t['nombre']} {t['dias']}d* → `{fmt_precio(nuevo)}`",
            parse_mode="Markdown", reply_markup=kb_admin())
        return

    # ── Agregar cuentas (texto) ────────────────────────────────────────────────
    if esp == "addcuenta":
        ctx.user_data.pop("adm_esperando")
        resultado = parsear_y_guardar_cuentas(text)
        await update.message.reply_text(resultado, parse_mode="Markdown", reply_markup=kb_admin())
        return

    # ── Buscar usuario ─────────────────────────────────────────────────────────
    if esp == "buscar_usuario":
        ctx.user_data.pop("adm_esperando")
        with get_conn() as c:
            if text.startswith("@"):
                u = c.execute("SELECT * FROM usuarios WHERE username=?", (text[1:],)).fetchone()
            elif text.isdigit():
                u = c.execute("SELECT * FROM usuarios WHERE user_id=?", (int(text),)).fetchone()
            else:
                u = c.execute(
                    "SELECT * FROM usuarios WHERE username LIKE ? OR nombre LIKE ?",
                    (f"%{text}%", f"%{text}%")
                ).fetchone()
            if not u:
                await update.message.reply_text("❌ Usuario no encontrado.", reply_markup=kb_admin()); return
            cuentas = c.execute(
                "SELECT * FROM cuentas WHERE entregado_a=? ORDER BY fecha_entrega DESC", (u["user_id"],)
            ).fetchall()

        estado = "🚫 Bloqueado" if u["bloqueado"] else "✅ Activo"
        uname  = f"@{u['username']}" if u["username"] else "sin username"
        info   = (
            f"🔍 *Perfil del usuario*\n\n"
            f"🆔 ID: `{u['user_id']}`\n"
            f"👤 {u['nombre'] or 'N/A'} ({uname})\n"
            f"💵 Créditos: `{fmt_precio(u['creditos'])}`\n"
            f"📅 Registrado: `{u['creado_en']}`\n"
            f"Estado: {estado}\n"
            f"📦 Cuentas compradas: `{len(cuentas)}`\n"
        )
        if cuentas:
            info += "\n*📋 Cuentas:*\n"
            for cu in cuentas[:10]:
                t = TIPOS.get(cu["tipo"], {})
                garantia_txt = f"   👤 `{cu['nombre_user']}`\n" if cu["nombre_user"] else ""
                info += (
                    f"\n{t.get('emoji','📦')} *{t.get('nombre','?')} {t.get('dias','')}d* | "
                    f"Fin: `{cu['fecha_fin'] or 'N/A'}`\n"
                    + garantia_txt +
                    f"   📧 `{(cu['correo'] if 'correo' in cu.keys() else cu['email'])}`\n"
                    f"   🔒 `{cu['contrasena']}`\n"
                )
        btns = [
            [InlineKeyboardButton(
                "🔄 Bloquear/Desbloquear",
                callback_data=f"adm_toggle_{u['user_id']}"
            )],
            [InlineKeyboardButton("⬅️ Volver", callback_data="adm_back")]
        ]
        await update.message.reply_text(info, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(btns))
        return

    # ── Dar créditos ───────────────────────────────────────────────────────────
    if esp == "dar_creditos":
        ctx.user_data.pop("adm_esperando")
        parts = text.split()
        if len(parts) != 2:
            await update.message.reply_text("❌ Formato: `<user_id> <monto>`",
                parse_mode="Markdown", reply_markup=kb_admin()); return
        try:
            target_id = int(parts[0])
            monto     = float(parts[1])
        except Exception:
            await update.message.reply_text("❌ Datos inválidos.", reply_markup=kb_admin()); return
        with get_conn() as c:
            u = c.execute("SELECT * FROM usuarios WHERE user_id=?", (target_id,)).fetchone()
            if not u:
                await update.message.reply_text("❌ Usuario no encontrado.", reply_markup=kb_admin()); return
            c.execute("UPDATE usuarios SET creditos=creditos+? WHERE user_id=?", (monto, target_id))
            c.execute(
                "INSERT INTO transacciones(user_id,tipo,monto,desc) VALUES(?,?,?,?)",
                (target_id, "recarga_admin", monto, "Admin recarga")
            )
        await update.message.reply_text(
            f"✅ `+{fmt_precio(monto)}` agregados a `{target_id}`",
            parse_mode="Markdown", reply_markup=kb_admin())
        return

    # ── Bloquear usuario ───────────────────────────────────────────────────────
    if esp == "bloquear":
        ctx.user_data.pop("adm_esperando")
        if not text.isdigit():
            await update.message.reply_text("❌ Envía un user_id numérico.", reply_markup=kb_admin()); return
        target = int(text)
        with get_conn() as c:
            u = c.execute("SELECT * FROM usuarios WHERE user_id=?", (target,)).fetchone()
            if not u:
                await update.message.reply_text("❌ No encontrado.", reply_markup=kb_admin()); return
            nuevo = 0 if u["bloqueado"] else 1
            c.execute("UPDATE usuarios SET bloqueado=? WHERE user_id=?", (nuevo, target))
        estado = "🚫 Bloqueado" if nuevo else "✅ Desbloqueado"
        await update.message.reply_text(f"{estado}: `{target}`",
            parse_mode="Markdown", reply_markup=kb_admin())
        return

    # ── Broadcast ──────────────────────────────────────────────────────────────
    if esp == "broadcast":
        ctx.user_data.pop("adm_esperando")
        with get_conn() as c:
            users = c.execute("SELECT user_id FROM usuarios WHERE bloqueado=0").fetchall()
        ok = fail = 0
        for u in users:
            try:
                await ctx.bot.send_message(chat_id=u["user_id"], text=text)
                ok += 1
            except Exception:
                fail += 1
        await update.message.reply_text(
            f"📢 Enviado: ✅ {ok} | ❌ {fail}",
            reply_markup=kb_admin())
        return

# ─── Handler de archivos .txt (admin) ─────────────────────────────────────────
async def adm_doc_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    doc = update.message.document
    if not doc or not doc.file_name.endswith(".txt"):
        return
    ctx.user_data.pop("adm_esperando", None)
    file    = await doc.get_file()
    content = await file.download_as_bytearray()
    texto   = content.decode("utf-8", errors="ignore")
    resultado = parsear_y_guardar_cuentas(texto)
    await update.message.reply_text(resultado, parse_mode="Markdown", reply_markup=kb_admin())

# ─── Router de callbacks ──────────────────────────────────────────────────────
async def callback_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data

    routes = {
        "mis_creditos":   cb_mis_creditos,
        "ver_precios":    cb_ver_precios,
        "pedir_menu":     cb_pedir_menu,
        "mis_cuentas":    cb_mis_cuentas,
        "canjear_prompt": cb_canjear_prompt,
        "menu_principal": cb_menu_principal,
        "adm_back":       adm_back,
        "adm_genkey":     adm_genkey,
        "adm_llaves":     adm_llaves,
        "adm_addcuenta":  adm_addcuenta,
        "adm_stock":      adm_stock,
        "adm_precios":    adm_precios,
        "adm_usuarios":   adm_usuarios,
        "adm_buscar":     adm_buscar,
        "adm_stats":      adm_stats,
        "adm_broadcast":  adm_broadcast_btn,
        "adm_creditos":   adm_creditos_btn,
        "adm_bloquear":   adm_bloquear_btn,
    }

    if data in routes:
        await routes[data](update, ctx)
    elif data.startswith("pedir_"):
        await cb_pedir_tipo(update, ctx)
    elif data.startswith("adm_toggle_"):
        await adm_toggle_user(update, ctx)

# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CallbackQueryHandler(callback_router))
    app.add_handler(MessageHandler(
        filters.Document.FileExtension("txt") & filters.User(ADMIN_ID),
        adm_doc_handler
    ))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.User(ADMIN_ID),
        msg_admin
    ))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        msg_usuario
    ))

    logger.info("🤖 Bot en marcha... (polling)")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()