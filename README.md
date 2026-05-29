# 🤖 Bot de Cuentas — Telegram

Sistema de entrega de cuentas (email + contraseña) con sistema de créditos por llave. El admin controla todo: genera llaves, carga el inventario y fija precios. Los usuarios solo pueden operar si tienen créditos.

---

## ✨ ¿Cómo funciona?

```
Admin genera llave  →  Usuario canjea llave  →  Recibe créditos
                                                      ↓
                                            Usuario pide cuenta
                                                      ↓
                                       Bot descuenta créditos y entrega
                                          📧 email + 🔒 contraseña
```

---

## 📋 Comandos

### Usuarios
| Comando | Descripción |
|---|---|
| `/start` | Menú principal con botones |
| `/creditos` | Ver saldo de créditos |
| `/precios` | Ver precios y stock disponible |
| `/canjear <LLAVE>` | Canjear una llave para recargar créditos |
| `/pedir individual` | Pedir una cuenta individual |
| `/pedir familiar` | Pedir una cuenta familiar |
| `/miscuentas` | Ver todas mis cuentas recibidas |

### Admin (solo tú)
| Comando | Descripción |
|---|---|
| `/admin` | Ver panel con todos los comandos |
| `/genkey 200` | Generar 1 llave de 200 créditos |
| `/genkey 200 5` | Generar 5 llaves de 200 créditos |
| `/genkey 200 3 VIP` | 3 llaves con nota "VIP" |
| `/llaves` | Ver llaves disponibles (no usadas) |
| `/addcuenta individual user@gmail.com Pass123` | Agregar cuenta al inventario |
| `/stock` | Ver inventario disponible vs vendido |
| `/setprecio individual 80` | Cambiar precio a 80 créditos |
| `/setprecio familiar 150` | Cambiar precio familiar |
| `/addcreditos 123456789 500` | Añadir 500 créditos a un usuario |
| `/usuarios` | Ver últimos 20 usuarios registrados |
| `/bloquear 123456789` | Bloquear / desbloquear usuario |
| `/stats` | Estadísticas generales |
| `/broadcast Hola a todos` | Enviar mensaje a todos los usuarios |

---

## 🚀 Despliegue en Railway

### 1. Crear el bot en Telegram
1. Abre Telegram → busca `@BotFather`
2. Escribe `/newbot` y sigue las instrucciones
3. Guarda el **Token** que te da (ej: `123456:ABCdef...`)

### 2. Obtener tu Telegram ID
1. Busca `@userinfobot` en Telegram
2. Escribe `/start`
3. Te dirá tu **ID numérico** (ej: `987654321`)

### 3. Subir el código a GitHub
```bash
git init
git add .
git commit -m "Bot de cuentas inicial"
git remote add origin https://github.com/tu-usuario/mi-bot.git
git push -u origin main
```

### 4. Crear proyecto en Railway
1. Ve a [railway.app](https://railway.app) → New Project
2. **Deploy from GitHub repo** → selecciona tu repositorio
3. Railway detectará automáticamente el proyecto Python

### 5. Agregar Variables de Entorno
En Railway → tu proyecto → **Variables**:

| Variable | Valor |
|---|---|
| `BOT_TOKEN` | El token de BotFather |
| `ADMIN_ID` | Tu ID numérico de Telegram |
| `DB_PATH` | `/data/bot.db` |

### 6. Agregar Volumen (para persistencia de datos)
> ⚠️ Sin esto, la base de datos se borra en cada redeploy.

1. Railway → tu proyecto → **+ New** → **Volume**
2. Mount Path: `/data`
3. Listo. La base de datos se guarda ahí permanentemente.

### 7. Deploy
Railway hace deploy automático. Ve a **Deployments** y revisa los logs. Deberías ver:
```
✅ Base de datos lista: /data/bot.db
🤖 Bot en marcha… (polling)
```

---

## 💡 Flujo de uso típico

**Tú (admin):**
```
/genkey 300 2
→ ABCD1234EFGH5678
→ WXYZ9876MNOP4321
```

**Le das esa llave al usuario (por cualquier medio).**

**El usuario en el bot:**
```
/canjear ABCD1234EFGH5678
→ ✅ +300 créditos

/pedir individual
→ 📧 email@gmail.com
→ 🔒 MiContraseña123
```

---

## 🗂️ Estructura del proyecto

```
├── main.py          ← Todo el código del bot
├── requirements.txt ← Dependencias Python
├── Procfile         ← Comando de inicio (Railway/Heroku)
├── railway.toml     ← Configuración Railway
└── .env.example     ← Variables de entorno de ejemplo
```

---

## ⚙️ Personalización

- **Cambiar precios por defecto**: edita las líneas `INSERT OR IGNORE INTO precios` en `init_db()` dentro de `main.py`
- **Agregar nuevo tipo de cuenta**: añade una fila en la tabla `precios` y agrega el emoji en el dict `EMOJIS`
- **Largo de las llaves**: cambia el parámetro `longitud` en `generar_llave()`
