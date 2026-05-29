#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════╗
║     BOT DE CUENTAS — TELEGRAM BOT            ║
║   Sistema de entrega con créditos por llave  ║
╚══════════════════════════════════════════════╝

Variables de entorno necesarias:
  BOT_TOKEN   → Token del bot (BotFather)
  ADMIN_ID    → Tu Telegram ID (usa @userinfobot)
  DB_PATH     → (opcional) Ruta de la DB. Default: /data/bot.db
"""

import os
import logging
import sqlite3
import random
import string
from functools import wraps
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# ═══════════════════════════════════════════════
#  CONFIGURACIÓN
# ═══════════════════════════════════════════════
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_ID  = int(os.environ.get("ADMIN_ID", "0"))
DB_PATH   = os.environ.get("DB_PATH", "/data/bot.db")

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

EMOJIS = {"individual": "👤", "familiar": "👨‍👩‍👧‍👦"}


# ═══════════════════════════════════════════════
#  BASE DE DATOS
# ═══════════════════════════════════════════════
def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS usuarios (
            user_id       INTEGER PRIMARY KEY,
            username      TEXT    NOT NULL DEFAULT '',
            nombre        TEXT    NOT NULL DEFAULT '',
            creditos      INTEGER NOT NULL DEFAULT 0,
            total_pedidos INTEGER NOT NULL DEFAULT 0,
            bloqueado     INTEGER NOT NULL DEFAULT 0,
            creado_en     TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS llaves (
            llave      TEXT    PRIMARY KEY,
            creditos   INTEGER NOT NULL,
            nota       TEXT,
            usada      INTEGER NOT NULL DEFAULT 0,
            usada_por  INTEGER,
            creada_en  TEXT    NOT NULL DEFAULT (datetime('now')),
            usada_en   TEXT
        );

        CREATE TABLE IF NOT EXISTS cuentas (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            tipo        TEXT    NOT NULL,
            email       TEXT    NOT NULL,
            password    TEXT    NOT NULL,
            asignada    INTEGER NOT NULL DEFAULT 0,
            asignada_a  INTEGER,
            asignada_en TEXT,
            agregada_en TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS precios (
            tipo        TEXT    PRIMARY KEY,
            precio      INTEGER NOT NULL,
            descripcion TEXT    NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS historial (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL,
            tipo            TEXT    NOT NULL,
            cuenta_id       INTEGER NOT NULL,
            creditos_usados INTEGER NOT NULL,
            fecha           TEXT    NOT NULL DEFAULT (datetime('now'))
        );
        """)

        # Precios por defecto (se pueden cambiar con /setprecio)
        conn.execute(
            "INSERT OR IGNORE INTO precios VALUES ('individual', 50, 'Cuenta Individual — 1 usuario')"
        )
        conn.execute(
            "INSERT OR IGNORE INTO precios VALUES ('familiar', 100, 'Cuenta Familiar — hasta 6 usuarios')"
        )
        conn.commit()

    logger.info("✅ Base de datos lista: %s", DB_PATH)


# ═══════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════
def generar_llave(longitud: int = 16) -> str:
    """Genera una llave aleatoria única."""
    chars = string.ascii_uppercase + string.digits
    return "".join(random.choices(chars, k=longitud))


def registrar_si_nuevo(user) -> None:
    """Crea el registro del usuario si aún no existe."""
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO usuarios (user_id, username, nombre) VALUES (?,?,?)",
            (user.id, user.username or "", user.full_name),
        )
        conn.commit()


def get_creditos(user_id: int) -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT creditos FROM usuarios WHERE user_id=?", (user_id,)
        ).fetchone()
    return row["creditos"] if row else 0


def get_precio(tipo: str) -> Optional[int]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT precio FROM precios WHERE tipo=?", (tipo,)
        ).fetchone()
    return row["precio"] if row else None


def is_bloqueado(user_id: int) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT bloqueado FROM usuarios WHERE user_id=?", (user_id,)
        ).fetchone()
    return bool(row and row["bloqueado"])


def admin_only(func):
    """Decorador: solo el admin puede usar estos comandos."""
    @wraps(func)
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_ID:
            await update.message.reply_text("❌ No tienes permiso para usar este comando.")
            return
        return await func(update, ctx)
    return wrapper


# ═══════════════════════════════════════════════
#  CORE — ENTREGA DE CUENTAS
# ═══════════════════════════════════════════════
async def _entregar_cuenta(target, user_id: int, tipo: str) -> None:
    """
    Verifica créditos, descuenta y entrega una cuenta disponible.
    `target` puede ser un Message o CallbackQuery.message.
    """
    if is_bloqueado(user_id):
        await target.reply_text("🚫 Tu acceso ha sido suspendido. Contacta al administrador.")
        return

    precio = get_precio(tipo)
    if precio is None:
        await target.reply_text("❌ Tipo de cuenta no disponible.")
        return

    creditos = get_creditos(user_id)
    if creditos < precio:
        falta = precio - creditos
        await target.reply_text(
            f"❌ *Créditos insuficientes*\n\n"
            f"💳 Tienes: *{creditos} créditos*\n"
            f"💲 Precio {tipo}: *{precio} créditos*\n"
            f"📉 Te faltan: *{falta} créditos*\n\n"
            f"Solicita una llave de recarga al administrador.",
            parse_mode="Markdown",
        )
        return

    with get_conn() as conn:
        cuenta = conn.execute(
            "SELECT id, email, password FROM cuentas WHERE tipo=? AND asignada=0 LIMIT 1",
            (tipo,),
        ).fetchone()

        if not cuenta:
            await target.reply_text(
                f"⚠️ *Sin stock* de cuentas *{tipo}* en este momento.\n"
                "Avisa al administrador para que agregue más.",
                parse_mode="Markdown",
            )
            return

        # Asignar cuenta + descontar créditos + registrar historial
        conn.execute(
            "UPDATE cuentas SET asignada=1, asignada_a=?, asignada_en=datetime('now') WHERE id=?",
            (user_id, cuenta["id"]),
        )
        conn.execute(
            "UPDATE usuarios SET creditos=creditos-?, total_pedidos=total_pedidos+1 WHERE user_id=?",
            (precio, user_id),
        )
        conn.execute(
            "INSERT INTO historial (user_id, tipo, cuenta_id, creditos_usados) VALUES (?,?,?,?)",
            (user_id, tipo, cuenta["id"], precio),
        )
        conn.commit()

    emoji      = EMOJIS.get(tipo, "📦")
    nuevo_saldo = get_creditos(user_id)

    await target.reply_text(
        f"✅ *¡Cuenta entregada!*\n\n"
        f"{emoji} Tipo: *{tipo.capitalize()}*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📧 Email:    `{cuenta['email']}`\n"
        f"🔒 Password: `{cuenta['password']}`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💳 Créditos restantes: *{nuevo_saldo}*\n\n"
        f"⚠️ Guarda estos datos en un lugar seguro.",
        parse_mode="Markdown",
    )


# ═══════════════════════════════════════════════
#  COMANDOS — USUARIOS
# ═══════════════════════════════════════════════
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    registrar_si_nuevo(user)

    if is_bloqueado(user.id):
        await update.message.reply_text("🚫 Tu acceso ha sido suspendido.")
        return

    creditos = get_creditos(user.id)
    kb = [
        [
            InlineKeyboardButton("💳 Mis Créditos",  callback_data="ver_creditos"),
            InlineKeyboardButton("💰 Precios",       callback_data="ver_precios"),
        ],
        [InlineKeyboardButton("🛒 Pedir Cuenta",     callback_data="menu_pedir")],
        [InlineKeyboardButton("📋 Mis Cuentas",      callback_data="mis_cuentas")],
    ]
    await update.message.reply_text(
        f"👋 ¡Hola, *{user.first_name}*!\n\n"
        f"💳 Créditos disponibles: *{creditos}*\n\n"
        f"📌 *Comandos:*\n"
        f"🔑 `/canjear <LLAVE>` — Activar llave\n"
        f"🛒 `/pedir individual` o `/pedir familiar`\n"
        f"💳 `/creditos` — Ver mi saldo\n"
        f"📋 `/miscuentas` — Ver mis cuentas\n"
        f"💰 `/precios` — Ver precios y stock",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb),
    )


async def cmd_creditos(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    registrar_si_nuevo(user)
    creds = get_creditos(user.id)
    await update.message.reply_text(
        f"💳 *Tu Saldo*\n\n"
        f"Disponibles: *{creds} créditos*\n\n"
        f"Para recargar:\n`/canjear <LLAVE>`",
        parse_mode="Markdown",
    )


async def cmd_precios(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    with get_conn() as conn:
        precios = conn.execute("SELECT tipo, precio, descripcion FROM precios").fetchall()
        disp    = {
            r["tipo"]: r["n"]
            for r in conn.execute(
                "SELECT tipo, COUNT(*) as n FROM cuentas WHERE asignada=0 GROUP BY tipo"
            ).fetchall()
        }

    lines = ["💰 *Lista de Precios*\n"]
    for p in precios:
        emoji = EMOJIS.get(p["tipo"], "📦")
        stock = disp.get(p["tipo"], 0)
        lines.append(
            f"{emoji} *{p['tipo'].capitalize()}*\n"
            f"💲 {p['precio']} créditos\n"
            f"📝 _{p['descripcion']}_\n"
            f"📦 Stock: *{stock} disponible(s)*\n"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_canjear(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    registrar_si_nuevo(user)

    if is_bloqueado(user.id):
        await update.message.reply_text("🚫 Tu acceso ha sido suspendido.")
        return

    if not ctx.args:
        await update.message.reply_text(
            "❌ Uso: `/canjear <LLAVE>`\n\nEjemplo: `/canjear ABC123XYZ456WXYZ`",
            parse_mode="Markdown",
        )
        return

    llave = ctx.args[0].upper().strip()

    with get_conn() as conn:
        row = conn.execute(
            "SELECT creditos, usada FROM llaves WHERE llave=?", (llave,)
        ).fetchone()

        if not row:
            await update.message.reply_text("❌ Llave inválida. Verifica e intenta de nuevo.")
            return

        if row["usada"]:
            await update.message.reply_text("❌ Esta llave ya fue canjeada anteriormente.")
            return

        creditos_llave = row["creditos"]

        conn.execute(
            "UPDATE llaves SET usada=1, usada_por=?, usada_en=datetime('now') WHERE llave=?",
            (user.id, llave),
        )
        conn.execute(
            "UPDATE usuarios SET creditos=creditos+? WHERE user_id=?",
            (creditos_llave, user.id),
        )
        conn.commit()

    nuevo_saldo = get_creditos(user.id)
    await update.message.reply_text(
        f"✅ *¡Llave canjeada exitosamente!*\n\n"
        f"🎁 Recibiste: *+{creditos_llave} créditos*\n"
        f"💳 Saldo actual: *{nuevo_saldo} créditos*",
        parse_mode="Markdown",
    )


async def cmd_pedir(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    registrar_si_nuevo(user)

    if is_bloqueado(user.id):
        await update.message.reply_text("🚫 Tu acceso ha sido suspendido.")
        return

    if not ctx.args:
        kb = [[
            InlineKeyboardButton("👤 Individual", callback_data="pedir_individual"),
            InlineKeyboardButton("👨‍👩‍👧‍👦 Familiar",  callback_data="pedir_familiar"),
        ]]
        await update.message.reply_text(
            "🛒 *¿Qué tipo de cuenta deseas?*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(kb),
        )
        return

    tipo = ctx.args[0].lower()
    if tipo not in ("individual", "familiar"):
        await update.message.reply_text(
            "❌ Tipo inválido. Usa: `individual` o `familiar`", parse_mode="Markdown"
        )
        return

    await _entregar_cuenta(update.message, user.id, tipo)


async def cmd_miscuentas(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    with get_conn() as conn:
        cuentas = conn.execute(
            "SELECT tipo, email, password, asignada_en "
            "FROM cuentas WHERE asignada_a=? ORDER BY asignada_en DESC",
            (user.id,),
        ).fetchall()

    if not cuentas:
        await update.message.reply_text(
            "📋 No tienes cuentas aún.\n\n"
            "Usa `/pedir individual` o `/pedir familiar` para obtener una.",
            parse_mode="Markdown",
        )
        return

    lines = [f"📋 *Tus Cuentas* ({len(cuentas)} total)\n"]
    for i, c in enumerate(cuentas, 1):
        emoji = EMOJIS.get(c["tipo"], "📦")
        fecha = (c["asignada_en"] or "")[:10]
        lines.append(
            f"*{i}. {emoji} {c['tipo'].capitalize()}*\n"
            f"📧 `{c['email']}`\n"
            f"🔒 `{c['password']}`\n"
            f"📅 {fecha}\n"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ═══════════════════════════════════════════════
#  COMANDOS — ADMINISTRADOR
# ═══════════════════════════════════════════════
@admin_only
async def cmd_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🔧 *Panel de Administración*\n\n"
        "🔑 *Llaves:*\n"
        "`/genkey <créditos> [cantidad] [nota]`\n"
        "`/llaves` — Ver llaves disponibles\n\n"
        "📦 *Cuentas:*\n"
        "`/addcuenta <tipo> <email> <pass>`\n"
        "`/stock` — Ver inventario\n\n"
        "💰 *Precios:*\n"
        "`/setprecio <tipo> <precio>`\n\n"
        "👥 *Usuarios:*\n"
        "`/usuarios` — Listar usuarios\n"
        "`/addcreditos <id> <cantidad>`\n"
        "`/bloquear <id>` — Bloquear / desbloquear\n\n"
        "📊 `/stats` — Estadísticas\n"
        "📣 `/broadcast <mensaje>` — Mensaje a todos",
        parse_mode="Markdown",
    )


@admin_only
async def cmd_genkey(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Generar llaves de recarga. Uso: /genkey <créditos> [cantidad] [nota]"""
    if not ctx.args:
        await update.message.reply_text(
            "❌ Uso: `/genkey <créditos> [cantidad] [nota]`\n\n"
            "Ejemplos:\n"
            "`/genkey 100` — 1 llave de 100 créditos\n"
            "`/genkey 200 5` — 5 llaves de 200 créditos\n"
            "`/genkey 500 3 VIP cliente` — 3 llaves con nota",
            parse_mode="Markdown",
        )
        return

    try:
        creditos = int(ctx.args[0])
        cantidad = int(ctx.args[1]) if len(ctx.args) > 1 else 1
        nota     = " ".join(ctx.args[2:]) if len(ctx.args) > 2 else None
        assert creditos > 0 and 1 <= cantidad <= 50
    except (ValueError, AssertionError):
        await update.message.reply_text("❌ Valores inválidos. Máximo 50 llaves a la vez.")
        return

    generadas = []
    with get_conn() as conn:
        for _ in range(cantidad):
            # Garantizar unicidad
            while True:
                k = generar_llave()
                if not conn.execute("SELECT 1 FROM llaves WHERE llave=?", (k,)).fetchone():
                    break
            conn.execute(
                "INSERT INTO llaves (llave, creditos, nota) VALUES (?,?,?)",
                (k, creditos, nota),
            )
            generadas.append(k)
        conn.commit()

    nota_txt = f"\n📝 Nota: _{nota}_" if nota else ""
    texto    = f"✅ *{cantidad} llave(s) generada(s) — {creditos} créditos c/u*{nota_txt}\n\n"
    texto   += "\n".join(f"`{k}`" for k in generadas)
    await update.message.reply_text(texto, parse_mode="Markdown")


@admin_only
async def cmd_addcuenta(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Agregar cuenta al stock. Uso: /addcuenta <tipo> <email> <password>"""
    if not ctx.args or len(ctx.args) < 3:
        await update.message.reply_text(
            "❌ Uso: `/addcuenta <tipo> <email> <password>`\n\n"
            "Tipos: `individual` | `familiar`\n\n"
            "Ejemplo:\n"
            "`/addcuenta individual user@gmail.com MiPass123`",
            parse_mode="Markdown",
        )
        return

    tipo  = ctx.args[0].lower()
    email = ctx.args[1]
    pwd   = " ".join(ctx.args[2:])   # permite espacios en la contraseña

    if tipo not in ("individual", "familiar"):
        await update.message.reply_text(
            "❌ Tipo inválido. Usa `individual` o `familiar`.", parse_mode="Markdown"
        )
        return

    with get_conn() as conn:
        conn.execute(
            "INSERT INTO cuentas (tipo, email, password) VALUES (?,?,?)",
            (tipo, email, pwd),
        )
        stock = conn.execute(
            "SELECT COUNT(*) as n FROM cuentas WHERE tipo=? AND asignada=0", (tipo,)
        ).fetchone()["n"]
        conn.commit()

    emoji = EMOJIS.get(tipo, "📦")
    await update.message.reply_text(
        f"✅ *Cuenta agregada al inventario*\n\n"
        f"{emoji} Tipo: *{tipo}*\n"
        f"📧 Email: `{email}`\n"
        f"📦 Stock actual ({tipo}): *{stock}*",
        parse_mode="Markdown",
    )


@admin_only
async def cmd_setprecio(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Cambiar precio. Uso: /setprecio <tipo> <precio>"""
    if not ctx.args or len(ctx.args) < 2:
        await update.message.reply_text(
            "❌ Uso: `/setprecio <tipo> <precio>`\n\n"
            "Ejemplo: `/setprecio familiar 120`",
            parse_mode="Markdown",
        )
        return

    tipo = ctx.args[0].lower()
    try:
        precio = int(ctx.args[1])
        assert precio > 0
    except (ValueError, AssertionError):
        await update.message.reply_text("❌ Precio inválido.")
        return

    with get_conn() as conn:
        if not conn.execute("SELECT 1 FROM precios WHERE tipo=?", (tipo,)).fetchone():
            await update.message.reply_text(
                f"❌ El tipo `{tipo}` no existe.", parse_mode="Markdown"
            )
            return
        conn.execute("UPDATE precios SET precio=? WHERE tipo=?", (precio, tipo))
        conn.commit()

    emoji = EMOJIS.get(tipo, "📦")
    await update.message.reply_text(
        f"✅ Precio actualizado\n\n{emoji} *{tipo}* → *{precio} créditos*",
        parse_mode="Markdown",
    )


@admin_only
async def cmd_addcreditos(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Añadir créditos manualmente. Uso: /addcreditos <user_id> <cantidad>"""
    if not ctx.args or len(ctx.args) < 2:
        await update.message.reply_text(
            "❌ Uso: `/addcreditos <user_id> <cantidad>`\n\n"
            "Obtén el ID del usuario con /usuarios",
            parse_mode="Markdown",
        )
        return

    try:
        target_id = int(ctx.args[0])
        cantidad  = int(ctx.args[1])
        assert cantidad != 0
    except (ValueError, AssertionError):
        await update.message.reply_text("❌ Valores inválidos.")
        return

    with get_conn() as conn:
        row = conn.execute(
            "SELECT nombre, creditos FROM usuarios WHERE user_id=?", (target_id,)
        ).fetchone()
        if not row:
            await update.message.reply_text("❌ Usuario no encontrado.")
            return
        conn.execute(
            "UPDATE usuarios SET creditos=creditos+? WHERE user_id=?",
            (cantidad, target_id),
        )
        conn.commit()

    nuevo   = row["creditos"] + cantidad
    signo   = "+" if cantidad > 0 else ""
    await update.message.reply_text(
        f"✅ *Créditos actualizados*\n\n"
        f"👤 *{row['nombre']}* (`{target_id}`)\n"
        f"📊 {signo}{cantidad} créditos\n"
        f"💳 Nuevo saldo: *{nuevo}*",
        parse_mode="Markdown",
    )


@admin_only
async def cmd_stock(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT tipo, "
            "  SUM(CASE WHEN asignada=0 THEN 1 ELSE 0 END) as disponibles, "
            "  SUM(asignada) as vendidas "
            "FROM cuentas GROUP BY tipo"
        ).fetchall()

    if not rows:
        await update.message.reply_text("📦 El inventario está vacío.")
        return

    lines = ["📦 *Inventario de Cuentas*\n"]
    for r in rows:
        emoji = EMOJIS.get(r["tipo"], "📦")
        lines.append(
            f"{emoji} *{r['tipo'].capitalize()}*\n"
            f"  ✅ Disponibles: *{r['disponibles']}*\n"
            f"  🛒 Vendidas:    *{r['vendidas']}*\n"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


@admin_only
async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    with get_conn() as conn:
        total_usuarios = conn.execute("SELECT COUNT(*) as n FROM usuarios").fetchone()["n"]
        total_ventas   = conn.execute("SELECT COUNT(*) as n FROM historial").fetchone()["n"]
        creditos_usados = conn.execute(
            "SELECT COALESCE(SUM(creditos_usados),0) as n FROM historial"
        ).fetchone()["n"]
        llaves_disp    = conn.execute("SELECT COUNT(*) as n FROM llaves WHERE usada=0").fetchone()["n"]
        llaves_usadas  = conn.execute("SELECT COUNT(*) as n FROM llaves WHERE usada=1").fetchone()["n"]
        bloqueados     = conn.execute("SELECT COUNT(*) as n FROM usuarios WHERE bloqueado=1").fetchone()["n"]

    await update.message.reply_text(
        f"📊 *Estadísticas Generales*\n\n"
        f"👥 Usuarios totales:     *{total_usuarios}*\n"
        f"🚫 Bloqueados:           *{bloqueados}*\n"
        f"🛒 Ventas realizadas:    *{total_ventas}*\n"
        f"💰 Créditos consumidos: *{creditos_usados}*\n\n"
        f"🔑 Llaves disponibles:  *{llaves_disp}*\n"
        f"🔑 Llaves usadas:       *{llaves_usadas}*",
        parse_mode="Markdown",
    )


@admin_only
async def cmd_llaves(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT llave, creditos, nota FROM llaves WHERE usada=0 "
            "ORDER BY creada_en DESC LIMIT 40"
        ).fetchall()

    if not rows:
        await update.message.reply_text(
            "🔑 No hay llaves disponibles.\n\nUsa `/genkey` para crear.", parse_mode="Markdown"
        )
        return

    lines = [f"🔑 *Llaves Disponibles* ({len(rows)})\n"]
    for r in rows:
        nota = f" — _{r['nota']}_" if r["nota"] else ""
        lines.append(f"`{r['llave']}` · {r['creditos']} créditos{nota}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


@admin_only
async def cmd_usuarios(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT user_id, nombre, username, creditos, total_pedidos, bloqueado "
            "FROM usuarios ORDER BY creado_en DESC LIMIT 20"
        ).fetchall()

    if not rows:
        await update.message.reply_text("👥 Aún no hay usuarios registrados.")
        return

    lines = [f"👥 *Usuarios Registrados* ({len(rows)})\n"]
    for r in rows:
        uname    = f"@{r['username']}" if r["username"] else "—"
        bloqueo  = " 🚫" if r["bloqueado"] else ""
        lines.append(
            f"• *{r['nombre']}* {uname}{bloqueo}\n"
            f"  `{r['user_id']}` | 💳 {r['creditos']} | 🛒 {r['total_pedidos']} pedidos"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


@admin_only
async def cmd_bloquear(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Bloquear o desbloquear usuario. Uso: /bloquear <user_id>"""
    if not ctx.args:
        await update.message.reply_text(
            "❌ Uso: `/bloquear <user_id>`\n\n"
            "Obtén el ID con /usuarios. El comando alterna entre bloquear/desbloquear.",
            parse_mode="Markdown",
        )
        return

    try:
        target_id = int(ctx.args[0])
    except ValueError:
        await update.message.reply_text("❌ ID inválido.")
        return

    with get_conn() as conn:
        row = conn.execute(
            "SELECT nombre, bloqueado FROM usuarios WHERE user_id=?", (target_id,)
        ).fetchone()
        if not row:
            await update.message.reply_text("❌ Usuario no encontrado.")
            return
        nuevo_estado = 0 if row["bloqueado"] else 1
        conn.execute(
            "UPDATE usuarios SET bloqueado=? WHERE user_id=?", (nuevo_estado, target_id)
        )
        conn.commit()

    estado = "🔒 *Bloqueado*" if nuevo_estado else "✅ *Desbloqueado*"
    await update.message.reply_text(
        f"{estado}: *{row['nombre']}* (`{target_id}`)", parse_mode="Markdown"
    )


@admin_only
async def cmd_broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Enviar mensaje a todos los usuarios. Uso: /broadcast <mensaje>"""
    if not ctx.args:
        await update.message.reply_text(
            "❌ Uso: `/broadcast <mensaje>`", parse_mode="Markdown"
        )
        return

    mensaje = " ".join(ctx.args)

    with get_conn() as conn:
        user_ids = [
            r["user_id"]
            for r in conn.execute(
                "SELECT user_id FROM usuarios WHERE bloqueado=0"
            ).fetchall()
        ]

    enviados = fallidos = 0
    for uid in user_ids:
        try:
            await ctx.bot.send_message(
                uid,
                f"📣 *Mensaje del administrador:*\n\n{mensaje}",
                parse_mode="Markdown",
            )
            enviados += 1
        except Exception:
            fallidos += 1

    await update.message.reply_text(
        f"📣 *Broadcast completado*\n\n✅ Enviados: {enviados}\n❌ Fallidos: {fallidos}",
        parse_mode="Markdown",
    )


# ═══════════════════════════════════════════════
#  CALLBACK — BOTONES INLINE
# ═══════════════════════════════════════════════
async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q    = update.callback_query
    user = q.from_user
    registrar_si_nuevo(user)
    await q.answer()

    if is_bloqueado(user.id):
        await q.message.reply_text("🚫 Tu acceso ha sido suspendido.")
        return

    data = q.data

    if data == "ver_creditos":
        creds = get_creditos(user.id)
        await q.message.reply_text(
            f"💳 *Tu Saldo:* *{creds} créditos*", parse_mode="Markdown"
        )

    elif data == "ver_precios":
        with get_conn() as conn:
            precios = conn.execute("SELECT tipo, precio, descripcion FROM precios").fetchall()
            disp    = {
                r["tipo"]: r["n"]
                for r in conn.execute(
                    "SELECT tipo, COUNT(*) as n FROM cuentas WHERE asignada=0 GROUP BY tipo"
                ).fetchall()
            }
        lines = ["💰 *Lista de Precios*\n"]
        for p in precios:
            emoji = EMOJIS.get(p["tipo"], "📦")
            lines.append(
                f"{emoji} *{p['tipo'].capitalize()}*\n"
                f"💲 {p['precio']} créditos | 📦 {disp.get(p['tipo'], 0)} en stock\n"
            )
        await q.message.reply_text("\n".join(lines), parse_mode="Markdown")

    elif data == "menu_pedir":
        kb = [[
            InlineKeyboardButton("👤 Individual", callback_data="pedir_individual"),
            InlineKeyboardButton("👨‍👩‍👧‍👦 Familiar",  callback_data="pedir_familiar"),
        ]]
        await q.message.reply_text(
            "🛒 *¿Qué tipo de cuenta deseas?*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(kb),
        )

    elif data in ("pedir_individual", "pedir_familiar"):
        tipo = data.split("_", 1)[1]
        await _entregar_cuenta(q.message, user.id, tipo)

    elif data == "mis_cuentas":
        with get_conn() as conn:
            cuentas = conn.execute(
                "SELECT tipo, email, password, asignada_en "
                "FROM cuentas WHERE asignada_a=? ORDER BY asignada_en DESC",
                (user.id,),
            ).fetchall()
        if not cuentas:
            await q.message.reply_text("📋 No tienes cuentas aún.")
            return
        lines = [f"📋 *Tus Cuentas* ({len(cuentas)})\n"]
        for c in cuentas:
            emoji = EMOJIS.get(c["tipo"], "📦")
            lines.append(
                f"{emoji} *{c['tipo'].capitalize()}*\n"
                f"📧 `{c['email']}`\n"
                f"🔒 `{c['password']}`\n"
            )
        await q.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ═══════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════
def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("❌ BOT_TOKEN no configurado. Revisa las variables de entorno.")
    if ADMIN_ID == 0:
        raise SystemExit("❌ ADMIN_ID no configurado. Revisa las variables de entorno.")

    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    # ── Comandos de usuario
    app.add_handler(CommandHandler("start",      cmd_start))
    app.add_handler(CommandHandler("creditos",   cmd_creditos))
    app.add_handler(CommandHandler("precios",    cmd_precios))
    app.add_handler(CommandHandler("canjear",    cmd_canjear))
    app.add_handler(CommandHandler("pedir",      cmd_pedir))
    app.add_handler(CommandHandler("miscuentas", cmd_miscuentas))

    # ── Comandos de admin
    app.add_handler(CommandHandler("admin",       cmd_admin))
    app.add_handler(CommandHandler("genkey",      cmd_genkey))
    app.add_handler(CommandHandler("addcuenta",   cmd_addcuenta))
    app.add_handler(CommandHandler("setprecio",   cmd_setprecio))
    app.add_handler(CommandHandler("addcreditos", cmd_addcreditos))
    app.add_handler(CommandHandler("stock",       cmd_stock))
    app.add_handler(CommandHandler("stats",       cmd_stats))
    app.add_handler(CommandHandler("llaves",      cmd_llaves))
    app.add_handler(CommandHandler("usuarios",    cmd_usuarios))
    app.add_handler(CommandHandler("bloquear",    cmd_bloquear))
    app.add_handler(CommandHandler("broadcast",   cmd_broadcast))

    # ── Botones inline
    app.add_handler(CallbackQueryHandler(handle_callback))

    logger.info("🤖 Bot en marcha… (polling)")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
