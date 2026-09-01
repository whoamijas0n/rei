# SYSTEM CONTEXT & DEVELOPER DIRECTIVES FOR PROJECT "REI" (v2.2)
> **Archivo de Contexto Permanente para Agentes de IA e IDEs de Desarrollo**  
> **Proyecto:** REI (Sistema Portátil de Diagnóstico Multi-Interfaz para Infraestructura TI & Endpoints)  
> **Estado del Sistema:** Producción / Hardware Embebido / Python 3.11+  
> **Arquitectura:** Asíncrona, Orientada a Objetos (POO), Plugin-Driven, Monolito Modular Desacoplado.

---

## 1. MISIÓN Y CONCEPTO DEL SISTEMA
**REI** es un dispositivo de campo de bolsillo, autónomo y de diagnóstico rápido (<25 segundos) diseñado para administradores de sistemas y técnicos de soporte de TI.  
Unifica en un único gadget portátil basado en **Raspberry Pi Zero 2 W**:

1. **Diagnóstico Asistido de Endpoints PC (Rubber Ducky HID):** Emulación de teclado USB HID (`/dev/hidg0`) con soporte multidioma (ES/US) para inyectar diagnósticos automatizados en computadoras cliente (Windows y Linux) sin requerir software preinstalado ni configuración previa por parte del usuario final.
2. **Infraestructura de Red:** Auditoría de switches, routers y firewalls vía consola Serie (RS232/RJ45) y protocolos SSH/SNMP.
3. **Análisis Inteligente con IA (Google Gemini API):** Procesamiento de telemetría recopilada mediante LLM (Gemini Free Tier) para síntesis ejecutiva, detección de causa raíz y recomendaciones accionables.
4. **Entrega de Reportes Móviles vía QR Dinámico:** Servidor local liviano (FastAPI) y generación de códigos QR de alta densidad en pantalla OLED para descarga inmediata de auditorías en el smartphone del técnico sin requerir conexión a internet.
5. **Selector Dinámico de Perfiles USB:** Conmutación por menú entre modo USB Convencional (Host/Almacenamiento) y modo Gadget USB Compuesto (Teclado HID + Red RNDIS/ECM).
6. **Interfaz Minimalista "Hero Cards":** Pantalla OLED SH1106 128x64 con interfaz visual de alto contraste controlada por Joystick de 5 direcciones y 3 botones físicos con bloqueo de seguridad durante tareas críticas.

---

## 2. ESPECIFICACIONES DE HARDWARE Y ENTORNO DE EJECUCIÓN (BOM)
Todo desarrollo debe apegarse estrictamente a las restricciones del hardware físico:

| Componente | Modelo / Chipset | Restricciones & Justificación Técnica |
| :--- | :--- | :--- |
| **SBC (Cerebro)** | **Raspberry Pi Zero 2 W** | CPU Broadcom BCM2710A1 Quad-Core 64-bit @ 1.0GHz. **RAM: 512MB LPDDR2**. Wi-Fi 802.11 b/g/n, BLE 4.2. |
| **Expansión I/O** | **Waveshare ETH/USB HUB HAT** | Controlador Ethernet RTL8152B (10/100M RJ45) + Hub USB 2.0 (3x USB Tipo-A). Expande el micro-USB nativo sin saturar el bus OTG. |
| **Pantalla OLED** | **1.3" I2C/SPI OLED HAT** | Controlador **SH1106** (128x64 px, 1-bit monocromático). Bus SPI primario (DC=GPIO24, RST=GPIO25, rotate=2) con fallback a I2C (0x3C). |
| **Controles Físicos** | **Joystick 5-Vías + 3 Botones** | Joystick: `UP`, `DOWN`, `LEFT`, `RIGHT`, `PRESS`. Botones: `KEY1`, `KEY2`, `KEY3`. Manejados por interrupciones GPIO (`gpiozero`). |
| **Batería & PMIC** | **PiSugar 3 UPS HAT** | Li-Po 1200mAh. Comunicación I2C (voltaje, %, corriente, temperatura). Botón de apagado seguro y RTC integrado. |
| **Consola Serial** | **Cable USB a RJ45 Cisco** | Chips FTDI FT232R / CP2102 asignado a `/dev/ttyUSB0` (9600 a 115200 bps). |
| **Almacenamiento** | **MicroSD 32GB Industrial** | Partición raíz `/` en **ReadOnly (OverlayFS)**. Partición persistente `/data` (ext4 journaled). `/tmp` y `/run` en **tmpfs (RAM)**. |

---

## 3. STACK TECNOLÓGICO, DEPENDENCIAS Y REGLAS DE LIBRERÍAS
Queda **estrictamente prohibido** el uso de librerías deprecadas, inestables o que causen bloqueos de concurrencia:

### 3.1. Librerías Obligatorias vs Prohibidas
* **Emulación USB HID & Ducky Payloads:**
  * ✅ **USAR:** Módulo nativo `/dev/hidg0` con tabla de mapeos de scancodes HID para teclados US e ISO Español (soporte de Dead Keys `~`, `^`, `´`, `` ` `` y modificadores Shift/AltGr).
* **Análisis con Inteligencia Artificial:**
  * ✅ **USAR:** `google-genai` (SDK oficial) o llamadas HTTP optimizadas a la API REST de Google Gemini (modelo `gemini-1.5-flash` / `gemini-2.0-flash` Free Tier) con timeout estricto de 10s.
* **Consola de Red & SSH:**
  * ✅ **USAR:** `netmiko` (v4.3+) o `scrapli`. ❌ **PROHIBIDO:** `paramiko` crudo sin wrapper de paginación.
* **Diagnóstico Windows Remoto (Fallback):**
  * ✅ **USAR:** `pypsrp` (PowerShell Remoting Protocol nativo sobre HTTP/HTTPS). ❌ **PROHIBIDO:** `pywinrm`.
* **Protocolo SNMP:**
  * ✅ **USAR:** `pysnmp-lextudio`. ❌ **PROHIBIDO:** `pysnmp` 4.4.x legacy.
* **Renderizado Gráfico & Códigos QR:**
  * ✅ **USAR:** `luma.oled` (`sh1106`), `Pillow` (PIL 10.2+) y `qrcode[pil]`.
* **Hardware GPIO:**
  * ✅ **USAR:** `gpiozero` con backend `RPi.GPIO` o `lgpio`.
* **Seguridad & Micro-Servidor Web:**
  * ✅ **USAR:** `cryptography` (AES-GCM-256), `fastapi`, `uvicorn`, `pydantic`.

### 3.2. requirements.txt Oficial
```text
luma.oled>=3.13.0
Pillow>=10.2.0
gpiozero>=2.0.1
netmiko>=4.3.0
scrapli>=2024.1.30
pypsrp>=0.8.1
pysnmp-lextudio>=6.1.2
pyserial>=3.5
cryptography>=42.0.5
fastapi>=0.110.0
uvicorn>=0.28.0
qrcode>=7.4.2
pydantic>=2.6.4
requests>=2.31.0
google-genai>=0.1.1
```

---

## 4. ESTRUCTURA MODULAR DEL REPOSITORIO
```text
/rei
│── main.py                  # Punto de entrada: Bootloader, ciclo de vida, hilos UI/Worker, orquestación de menús.
│── install.sh               # Script de autoinstalación, aprovisionamiento de SO, servicios systemd y rollback.
│── requirements.txt         # Especificación de dependencias Python fijadas.
│── config/
│   ├── settings.json        # Configuración operacional (GPIO, timeouts, API Keys de Gemini, brillo OLED, IPs).
│   └── credentials.json     # Bóveda cifrada (AES-GCM-256) para credenciales de red y tokens.
│── /core
│   ├── __init__.py
│   ├── manager.py           # Orquestador del sistema: ThreadPoolExecutor, TaskQueue, ResultQueue, carga dinámica.
│   ├── interfaces.py        # Modelos de dominio (DiagnosticResult, DiagnosticMetric, IDiagnosticPlugin).
│   ├── ducky.py             # Motor Rubber Ducky: Inyector HID (/dev/hidg0), layouts ES/US, gestor de secuencias de teclas.
│   ├── gemini_analyzer.py   # Cliente de Inteligencia Artificial: Integración con Gemini Free API para diagnósticos y soluciones.
│   ├── usb_modes.py         # Administrador de perfiles USB Gadget (ConfigFS / dwc2): Modo Normal vs Teclado HID.
│   ├── web_server.py        # Micro-servidor FastAPI local para recolección de reportes de endpoints y visor QR móvil.
│   └── parser.py            # Utilidades de parsing eficiente en memoria: generadores (yield), strip de secuencias ANSI.
│── /ui
│   ├── __init__.py
│   ├── display.py           # Motor de renderizado SH1106 (HeroCardDeckView, DetailCardView, UpdateProgressView, QRCodeView).
│   ├── input_handler.py     # Manejador GPIO para Joystick y Botones con debouncing de 200ms.
│   └── keyboard_view.py     # Teclado virtual OLED para introducción de texto/claves en pantalla.
│── /plugins
│   ├── __init__.py
│   ├── base_plugin.py       # Clase abstracta BasePlugin.
│   ├── network/             # Plugins para infraestructura de red (Cisco Serial, SSH, SNMP).
│   │   ├── cisco_ios.py
│   │   └── snmp_scanner.py
│   └── endpoints/           # Plugins para análisis de estaciones de trabajo y servidores.
│       ├── hid_windows.py   # Payloads HID & recolección de datos para Windows (PowerShell).
│       └── hid_linux.py     # Payloads HID & recolección de datos para Linux (Bash).
```

---

## 5. FLUJO DE TRABAJO PARA DIAGNÓSTICO DE ENDPOINTS PC (RUBBER DUCKY HID)

### 5.1. Árbol de Navegación del Menú "ENDPOINTS PC"
```text
[MENÚ PRINCIPAL]
  └── [ENDPOINTS PC] (Hero Card)
        ├── [WINDOWS HOST] (Hero Card)
        │     └── [SELECCIÓN DE TECLADO] (Hero Card)
        │           ├── [TECLADO ESPAÑOL] (Layout 'es' con Dead Keys y AltGr)
        │           └── [TECLADO INGLES]  (Layout 'us')
        │                 └── [TIPO DE PROBLEMA] (Hero Cards)
        │                       ├── [RED / CONEXION]      ──> Inyección Payload Red (Ping, DNS, IP, Adapter)
        │                       ├── [HARDWARE / CPU]     ──> Inyección Payload HW (Temp, RAM, SMART, Throttling)
        │                       ├── [ANALISIS MALWARE]   ──> Inyección Payload Malware (Procesos, Startup, Defender)
        │                       ├── [OTROS PROBLEMAS]    ──> Inyección Payload Logs (EventLog, Disk Space, Crash)
        │                       └── [ANALISIS COMPLETO]  ──> Inyección Payload Suite Completa
        │                             │
        │                             ▼
        │                     [PANTALLA DE PROGRESO (BLOQUEADA)]
        │                     - Barra de progreso interactiva (0% -> 100%)
        │                     - Navegación bloqueda (ignora joystick/botones)
        │                     - Validación de éxito o reporte de error en OLED
        │                             │
        │                             ▼
        │                     [SUBMENÚ POST-DIAGNÓSTICO] (Hero Cards)
        │                       ├── [ANALISIS CON IA]   ──> Envío a Gemini API + Barra de Progreso
        │                       └── [INFORME SIN IA]    ──> Formateo directo de telemetría local
        │                             │
        │                             ▼
        │                     [PANTALLA CÓDIGO QR]
        │                     - QR en pantalla SH1106 para escanear con móvil
        │                     - Descarga inmediata de reporte completo vía SoftAP/Web
        │
        └── [LINUX HOST] (Hero Card)
              └── (Mismo flujo: Selección Teclado -> Tipo Problema -> Progreso -> IA/Sin IA -> QR)
```

### 5.2. Mecanismo de Inyección y Exfiltración de Datos del Host
1. **Inyección HID (`/dev/hidg0`):**
   * **Windows:** Emula `GUI + r` -> ejecuta comando PowerShell en segundo plano (`powershell -WindowStyle Hidden -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "..."`).
   * **Linux:** Emula `Ctrl + Alt + t` (o terminal handler) -> ejecuta one-liner bash en segundo plano.
2. **Canal de Recepción en REI (`core/web_server.py`):**
   * El script inyectado recolecta la información y la envía mediante un `HTTP POST` con JSON al servidor FastAPI local de REI (`http://10.0.0.1:8000/api/v1/endpoint/report` o IP de red local), o la deja en un buffer compartido si se utiliza almacenamiento virtual.
   * La recepción de datos activa el evento `COMPLETED` en el `DiagnosticManager`.
3. **Bloqueo Estricto de UI:**
   * Mientras la tarea está en curso (`is_running=True`), la vista `UpdateProgressView` / `ExecutionProgressView` consume los eventos de entrada retornando `ViewActionType.NONE`. Se impide volver atrás (`pop_view`) o saltar entre menús para evitar estados inconsistentes.
   * Si ocurre un timeout o error de hardware, la vista pasa a estado de fallo (`is_finished=True, is_success=False`) y desbloquea `KEY3/PRESS` para salir.

### 5.3. Análisis Asistido con Inteligencia Artificial (`core/gemini_analyzer.py`)
* Si el técnico escoge **"ANALISIS CON IA"**:
  1. Se despliega una barra de progreso indicando `"Consultando IA..."`.
  2. Se envía la telemetría en JSON a la API de Gemini (usando la API Key configurada en `settings.json`).
  3. El prompt instruye a Gemini para entregar:
     - **Resumen Ejecutivo:** Diagnóstico del estado del equipo en 3 líneas.
     - **Causa Raíz:** Problemas identificados y severidad.
     - **Plan de Acción / Soluciones:** Pasos técnicos detallados para resolver la falla.
  4. El resultado enriquecido se adjunta al reporte final servido vía QR.

---

## 6. CONFIGURACIÓN DE MODOS USB EN UTILIDADES (`core/usb_modes.py`)
Dentro del menú `"UTILIDADES"`, se añade la opción `"MODO USB"` estructurada en Hero Cards:

* **[MODO USB NORMAL]:** Configura el controlador `dwc2` en modo host o almacenamiento estándar. Deshabilita el servicio gadget.
* **[MODO TECLADO HID]:** Configura `dwc2` en modo periférico y genera el script `libcomposite` con descriptores HID de teclado (`functions/hid.usb0`) y red Ethernet virtual (`rndis` / `ecm`).
* **Aplicación y Resiliencia:** El cambio actualiza `/usr/local/bin/usb_gadget.sh`, habilita `usb_gadget.service` y notifica al usuario si requiere reinicio del sistema o recarga de UDC en caliente.

---

## 7. CONTRATO DE DATOS Y DOMAIN MODELS (`core/interfaces.py`)
Todo diagnóstico de red o endpoint debe encapsularse en las siguientes estructuras fuertemente tipadas:

```python
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Dict, Any
import time

class Severity(Enum):
    OK = "OK"        # Estado nominal [✓]
    INFO = "INFO"    # Notificación informativa [i]
    WARNING = "WARN" # Advertencia / Umbral superado [!]
    CRITICAL = "CRIT"# Falla crítica / Hardware en riesgo [x]

@dataclass
class DiagnosticMetric:
    name: str                        # Ej: "Latencia Puerta Enlace", "Temp CPU", "Amenazas Detectadas"
    value: str                       # Ej: "12ms (OK)", "88 C (ALERTA)", "0 detectadas"
    status: Severity                 # Severidad individual
    details: Optional[str] = None    # Explicación contextual

@dataclass
class DiagnosticResult:
    plugin_name: str                 # Identificador único del plugin
    target_identifier: str           # "Windows Host (ES)", "Linux Host", "192.168.1.1"
    execution_time_ms: int           # Tiempo total de recolección
    overall_status: Severity         # Severidad global consolidada
    summary: str = ""                # Resumen de una línea para tarjetas Hero
    metrics: List[DiagnosticMetric] = field(default_factory=list)
    raw_output: Optional[str] = None # Captura íntegra para descarga Web/QR
    ai_analysis: Optional[Dict[str, Any]] = None # Resumen, causas y soluciones generadas por IA
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
```

---

## 8. ARQUITECTURA DE CONCURRENCIA Y THREADING
1. **UI Event Loop (`ui/display.py`):**
   * Ejecuta a **30 FPS continuos** sin bloqueos.
   * Renderiza el framebuffer monocromático y consulta resultados con `result_queue.get_nowait()`.
   * **PROHIBIDO:** Ejecutar sockets, sleeps prolongados, llamadas HTTP o escritura en `/dev/hidg0` dentro del hilo principal de la UI.
2. **Diagnostic Worker Pool (`core/manager.py`):**
   * Gestionado mediante `ThreadPoolExecutor(max_workers=2)`.
   * Procesa la cola de tareas asíncronas y emite eventos: `STATUS_INJECTING_HID`, `STATUS_AWAITING_PAYLOAD`, `STATUS_ANALYZING_AI`, `COMPLETED`, `TIMEOUT_ERROR`.
3. **Memoria y Recursos (<512MB RAM):**
   * No almacenar grandes volcados de logs en cadenas no estructuradas.
   * Utilizar generadores y buffers eficientes para evitar picos de recolección de basura.

---

## 9. ESTÁNDAR DE INTERFAZ GRÁFICA: "MINIMALIST HERO CARD SYSTEM"
Toda pantalla cumple con la geometría de renderizado definida para el display OLED SH1106 (128x64 px):

```text
+-------------------------------------------------------------------+
| [1,1]                                                  ••o•• [126,1] |
|                                                                   |
|                           [  ICONO  ]                             |
|                           [ 20 x 20 ]                             |
|                            (cx=64, cy=24)                         |
|                                                                   |
|                         TÍTULO EN MAYÚSCULAS                      |
|                               (y=44)                              |
|                                                                   |
+-------------------------------------------------------------------+
[1,62]                                                       [126,62]
```

### Reglas de Renderizado:
1. **Marco Continuo:** Borde perimetral fijo de 1px `(1, 1, 126, 62)`.
2. **Micro-Puntos (Dots):** Indicador de carrusel en la esquina superior derecha (`x=122 - total*5`, `y=4..6`).
3. **Icono Pixel-Art:** 20x20 px centrado en `(64, 24)`.
4. **Título Centrado:** Texto en mayúsculas a `y=44`, centrado dinámicamente según la longitud del texto.
5. **Navegación Intuitiva:**
   * `JOYSTICK LEFT / RIGHT`: Navegar entre tarjetas.
   * `JOYSTICK PRESS / KEY1`: Seleccionar / Ejecutar.
   * `KEY3`: Volver (`pop_view`).
   * `KEY2`: Acción secundaria / Ver código QR.

---

## 10. REGLAS PARA EL AGENTE AL GENERAR O MODIFICAR CÓDIGO
1. **Tipado Estricto (Type Hinting):** Todos los módulos deben utilizar anotaciones de tipo completas (`Optional`, `Union`, `List`, `Dict`, `Callable`, `Tuple`).
2. **Manejo Riguroso de Excepciones:**
   * Prohibido capturar `except Exception: pass` genérico en silencio.
   * Manejar de forma aislada excepciones de I/O, timeouts HID, errores HTTP de Gemini API y fallos de sockets.
3. **Aislamiento para Pruebas (Dry-Run & Mocking):**
   * Comandos de hardware o apagado (`poweroff`, `reboot`, configuración de kernel dwc2) deben soportar variables de entorno `REI_DRY_RUN=1` y `REI_MOCK_POWER=1` para validación segura en entornos de desarrollo x86_64.
4. **Sincronización con `install.sh` y `requirements.txt`:**
   * Toda nueva librería debe registrarse en `requirements.txt`.
   * Toda nueva regla de sistema operativo o servicio debe integrarse tanto en la rutina de instalación (`do_install`) como en la de reversión (`do_uninstall`) de `install.sh`.