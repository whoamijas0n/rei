# SYSTEM CONTEXT & DEVELOPER DIRECTIVES FOR PROJECT "REI" (v2.1)
> **Archivo de Contexto Permanente para Agentes de IA e IDEs de Desarrollo**  
> **Proyecto:** REI (Sistema Portátil de Diagnóstico Multi-Interfaz para Infraestructura TI & Endpoints)  
> **Estado del Sistema:** Producción / Hardware Embebido / Python 3.11+  
> **Arquitectura:** Asíncrona, Orientada a Objetos (POO), Plugin-Driven, Monolito Modular Desacoplado.

---

## 1. MISIÓN Y CONCEPTO DEL SISTEMA
**REI** es un dispositivo de campo de bolsillo, autónomo y de diagnóstico rápido (<25 segundos) diseñado para administradores de sistemas y de redes.  
Resuelve la dispersión de herramientas de diagnóstico al unificar en un único gadget portátil:
1. **Infraestructura de Red:** Auditoría de switches, routers y firewalls vía consola Serie (RS232/RJ45) y SSH/SNMP.
2. **Endpoints & Servidores:** Auditoría de hardware, sistema operativo, almacenamiento (SMART), memoria y logs en estaciones de trabajo (Windows) y servidores (Linux) mediante conexión USB directa (modo Gadget RNDIS/CDC-ECM) o interfaces de red.
3. **Interfaz Local Minimalista:** Pantalla OLED SH1106 128x64 con sistema "Minimalist Hero Cards" controlado por Joystick de 5 direcciones y 3 botones de hardware.
4. **Resiliencia Operativa:** Sistema operativo en memoria de solo lectura (OverlayFS), batería Li-Po integrada con telemetría I2C, y servidor Web/QR temporal para exportación móvil offline sin internet.

---

## 2. ESPECIFICACIONES DE HARDWARE Y ENTORNO DE EJECUCIÓN (BOM)
Cualquier código generado debe ser 100% compatible y optimizado para las restricciones del hardware físico:

| Componente | Modelo / Chipset | Restricciones & Especificaciones Técnicas |
| :--- | :--- | :--- |
| **SBC (Cerebro)** | **Raspberry Pi Zero 2 W** | CPU Broadcom BCM2710A1 Quad-Core 64-bit @ 1.0GHz. **RAM: 512MB LPDDR2**. Wi-Fi 802.11 b/g/n, BLE 4.2. |
| **Expansión I/O** | **Waveshare ETH/USB HUB HAT** | Controlador Ethernet RTL8152B (10/100M RJ45) + Hub USB 2.0 (3x USB Tipo-A). Resuelve la limitación de 1 solo micro-USB nativo. |
| **Pantalla OLED** | **1.3" I2C/SPI OLED HAT** | Controlador **SH1106** (128x64 píxeles, 1-bit monocromático). Bus SPI/I2C (0x3C). |
| **Controles Físicos** | **Joystick 5-Vías + 3 Botones** | Joystick: `UP`, `DOWN`, `LEFT`, `RIGHT`, `PRESS` (centro). Botones: `KEY1`, `KEY2`, `KEY3`. Manejados por interrupciones GPIO (`gpiozero`). |
| **Batería & PMIC** | **PiSugar 3 UPS HAT** | Li-Po 1200mAh. Comunicación I2C (voltaje, %, temperatura). RTC integrado y botón de apagado por hardware. |
| **Consola Serial** | **Cable Serial USB a RJ45** | Chips FTDI FT232R / CP2102. Asignado en `/dev/ttyUSB0` (Baudrates: 9600 a 115200 bps). |
| **Almacenamiento** | **MicroSD 32GB Industrial** | Partición raíz `/` en **ReadOnly (OverlayFS)**. Partición `/data` dedicada para persistencia segura. `/tmp` y `/run` en **tmpfs (RAM)**. |

---

## 3. STACK TECNOLÓGICO, DEPENDENCIAS Y REGLAS DE LIBRERÍAS
Queda **estrictamente prohibido** el uso de librerías deprecadas, inestables o que causen fugas de memoria en entornos concurrentes:

### 3.1. Librerías Obligatorias vs Prohibidas
* **SSH & Consola de Red:**  
  * ✅ **USAR:** `netmiko` (v4.3+) o `scrapli` (abstracción de prompts, paginación `--More--`, timeouts configurables para 120+ plataformas).  
  * ❌ **PROHIBIDO:** `paramiko` crudo sin wrapper de paginación o manejo de buffers.
* **Diagnóstico Windows / WinRM:**  
  * ✅ **USAR:** `pypsrp` (PowerShell Remoting Protocol nativo sobre HTTP/HTTPS, serialización CLIXML, máxima velocidad).  
  * ❌ **PROHIBIDO:** `pywinrm` (inestable, problemas de codificación y bajo rendimiento).
* **Protocolo SNMP:**  
  * ✅ **USAR:** `pysnmp-lextudio` (fork oficial mantenido y compatible con Python 3.11+, sin leaks en sockets).  
  * ❌ **PROHIBIDO:** `pysnmp` 4.4.x legacy (incompatible con Python 3.11+, bloquea el event loop).
* **Puerto Serie:**  
  * ✅ **USAR:** `pyserial` encapsulado en wrapper con auto-detección de baudios y limpieza regex de secuencias ANSI/VT100.
* **Renderizado Gráfico & UI:**  
  * ✅ **USAR:** `luma.oled` (con driver `sh1106`), `Pillow` (PIL) para dibujo 1-bit procedural.
* **Hardware GPIO:**  
  * ✅ **USAR:** `gpiozero` con backend `RPi.GPIO` o `lgpio`.
* **Criptografía & Seguridad:**  
  * ✅ **USAR:** `cryptography.hazmat.primitives.ciphers.aead.AESGCM` y `PBKDF2HMAC`.
* **Micro-Servidor Companion & QR:**  
  * ✅ **USAR:** `fastapi`, `uvicorn`, `qrcode[pil]`, `pydantic`.

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
```

---

## 4. ESTRUCTURA COMPLETA DEL REPOSITORIO
El proyecto `/rei` debe mantener rigurosamente la siguiente estructura modular:

```text
/rei
│── main.py                  # Punto de entrada: Bootloader, ciclo de vida, hilos UI/Worker, manejo global de señales/excepciones.
│── install.sh               # Script de autoinstalación, configuración de SO (systemd autostart como root) y rollback/desinstalación.
│── requirements.txt         # Especificación de dependencias Python fijadas.
│── config/
│   ├── settings.json        # Configuración operacional (I2C addr, pines GPIO, timeouts, brillo OLED, red virtual).
│   └── credentials.json     # Bóveda cifrada (AES-GCM-256) con usuarios/contraseñas de red y endpoints.
│── /core
│   ├── __init__.py
│   ├── manager.py           # Orquestador del sistema: Worker Pool (ThreadPoolExecutor), TaskQueue, ResultQueue, carga dinámica de plugins.
│   ├── interfaces.py        # Clases base abstractas y adaptadores de transporte (SSHTransport, SerialTransport, WinRMTransport, SNMPTransport).
│   └── parser.py            # Utilidades de parsing eficiente en memoria: generadores (yield), strip de secuencias ANSI, parsing JSON/CLIXML.
│── /ui
│   ├── __init__.py
│   ├── display.py           # Motor de renderizado SH1106 (HeroCardDeckView, DetailCardView, QRView, StatusBar) a 30 FPS.
│   └── input_handler.py     # Manejador de eventos GPIO para Joystick y Botones con debouncing y despacho no-bloqueante.
│── /plugins                 # Directorio modular de plugins (desacoplado del Core).
│   ├── __init__.py
│   ├── base_plugin.py       # Clase abstracta BasePlugin con contrato de datos estandarizado.
│   ├── network/             # Plugins para infraestructura de red.
│   │   ├── __init__.py
│   │   ├── cisco_ios.py     # Diagnóstico Cisco IOS (salud CPU, fuentes de poder, errores CRC en puertos, logs críticos).
│   │   └── mikrotik_routeros.py # Diagnóstico MikroTik RouterOS (recursos, caídas de enlaces, descartes de firewall).
│   └── endpoints/           # Plugins para servidores y estaciones de trabajo.
│       ├── __init__.py
│       ├── windows_pc.py    # Diagnóstico Windows vía PyPSRP/USB Gadget (SMART disco, RAM, eventos críticos EventLog).
│       └── linux_server.py  # Diagnóstico Linux vía SSH/USB Gadget (servicios systemd fallidos, dmesg, carga CPU/RAM, storage).
```

---

## 5. CONTRATO DE DATOS Y DOMAIN MODELS (`core/interfaces.py` / `plugins/base_plugin.py`)
Todo plugin debe retornar obligatoriamente una instancia de `DiagnosticResult` conformada por `DiagnosticMetric`. No se permiten diccionarios planos no tipados.

```python
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Dict, Any

class Severity(Enum):
    OK = "OK"        # Estado nominal [✓]
    INFO = "INFO"    # Notificación informativa [i]
    WARNING = "WARN" # Advertencia / Umbral superado [!]
    CRITICAL = "CRIT"# Falla crítica / Hardware en riesgo [x]

@dataclass
class DiagnosticMetric:
    name: str                        # Ej: "SMART Health", "Port Gi0/1 CRC", "CPU Load"
    value: str                       # Ej: "PASSED", "1,420 (High)", "94%"
    status: Severity                 # Severidad evaluada automáticamente
    details: Optional[str] = None    # Detalle contextual para vista expandida o QR

@dataclass
class DiagnosticResult:
    plugin_name: str                 # Identificador único del plugin
    target_identifier: str           # IP, Hostname, o puerto Serial (/dev/ttyUSB0)
    execution_time_ms: int           # Tiempo total de recolección en milisegundos
    overall_status: Severity         # Severidad global (la peor de las métricas contenidas)
    metrics: List[DiagnosticMetric] = field(default_factory=list)
    raw_output: Optional[str] = None # Captura íntegra del terminal/output para volcado Web/QR
    metadata: Dict[str, Any] = field(default_factory=dict)
```

---

## 6. ARQUITECTURA DE CONCURRENCIA Y THREADING (REGLA CRÍTICA DE ORO)
1. **Hilo Principal / UI Loop (`ui/display.py`):**
   * Corre estrictamente a **30 FPS**.
   * Solo realiza operaciones de dibujo en el framebuffer y consulta periódicamente de forma no bloqueante (`result_queue.get_nowait()`).
   * **PROHIBIDO:** Ejecutar llamadas de red (`socket`, `ssh`, `serial`, `requests`, `pypsrp`) dentro del hilo de la UI.
2. **Diagnostic Worker Pool (`core/manager.py`):**
   * Basado en `concurrent.futures.ThreadPoolExecutor(max_workers=2)`.
   * Lee tareas desde `TaskQueue` y deposita eventos granulares en `ResultQueue`:
     * Eventos emitidos: `STATUS_CONNECTING`, `STATUS_AUTHENTICATING`, `METRIC_RECEIVED`, `COMPLETED`, `TIMEOUT_ERROR`, `AUTH_ERROR`.
3. **Manejo de Memoria (<512MB RAM):**
   * Prohibido acumular volcados masivos en cadenas gigantescas.
   * Utilizar generadores (`yield`) en `core/parser.py` para procesar flujos de texto línea a línea.

---

## 7. ESTÁNDAR DE INTERFAZ GRÁFICA: "MINIMALIST HERO CARD SYSTEM" (128x64 OLED SH1106)
La UI sigue un diseño funcional de alta visibilidad para trabajo de campo en racks oscuros:

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

### Reglas Visuales Obligatorias de Renderizado:
1. **Borde Perimetral:** Rectángulo continuo de 1px en coordenadas `(1, 1, 126, 62)`.
2. **Paginación por Micro-Puntos (Dots):** Indicador minimalista en la esquina superior derecha (`y=4..6`) indicando posición en el carrusel de tarjetas.
3. **Composición Hero:** Icono Pixel-Art centralizado de 20x20 px en `(64, 24)` y título en mayúsculas centrado en `y=44`.
4. **Mapeo de Controles:**
   * `JOYSTICK LEFT / RIGHT`: Cambiar de tarjeta/carrusel anterior o siguiente.
   * `JOYSTICK PRESS` o `KEY1`: Seleccionar / Ejecutar acción.
   * `KEY3`: Volver / Cancelar (`pop_view()`).
   * `KEY2`: Acción secundaria / Alternar a código QR.
5. **Cero Texto Basura:** No incluir leyendas explicativas obvias tipo "Presione Enter para continuar" que consuman píxeles útiles.

---

## 8. ESTRATEGIA DE RED VIRTUAL USB GADGET (PUNTO A PUNTO)
Para diagnosticar PCs o servidores sin depender de switches corporativos con 802.1X o aislamiento de puertos (Port Isolation):
1. **ConfigFS / libcomposite:** La Raspberry Pi Zero 2 W se presenta ante el puerto USB de la máquina como una tarjeta de red virtual **RNDIS / CDC-ECM**.
2. **Subred Privada Embebida:** Un daemon local `dnsmasq` asigna instantáneamente:
   * **REI IP:** `10.0.0.1/30`
   * **Host Objetivo IP:** `10.0.0.2/30`
3. **Ejecución de Diagnóstico:** La comunicación WinRM (`pypsrp`) o SSH se realiza de inmediato a través de `10.0.0.2` a velocidad de bus USB 2.0 (480 Mbps teóricos).
4. **Almacenamiento Virtual / Mass Storage:** Se expone una partición de solo lectura con scripts de auditoría rápida (`audit.ps1`, herramientas portables) para ejecución local si la máquina tiene puertos de administración deshabilitados.
5. **USB HID Automatizado:** Capacidad de inyectar combinaciones de teclas bajo confirmación del usuario para habilitar WinRM en Windows en 3 segundos (`powershell Start-Process powershell -Verb runAs ...`).

---

## 9. SEGURIDAD Y BÓVEDA DE CREDENCIALES (`config/credentials.json`)
1. **Cifrado Fuerte:** Toda contraseña de red (Cisco enable, SSH roots, Windows local admins) se almacena cifrada mediante **AES-GCM-256**.
2. **Derivación de Llave:** `PBKDF2HMAC-SHA256` con **600,000 iteraciones** a partir del PIN maestro introducido por el usuario con el Joystick.
3. **Cero Persistencia de Secretos en Disco:**  
   * Las llaves y secretos desencriptados **residen exclusivamente en memoria volátil (`tmpfs` / `/tmp`)** y se destruyen tras 15 minutos de inactividad o al apagar el dispositivo.
   * **PROHIBIDO:** Escribir contraseñas en texto plano en logs, salidas de consola o archivos de configuración no cifrados.

---

## 10. REGLAS PARA EL AGENTE AL GENERAR O MODIFICAR CÓDIGO
Cualquier modificación o adición de código por parte de la IA debe cumplir con las siguientes restricciones:

1. **Tipado Estricto & Docstrings:** Todo método y función debe usar `typing` (`Union`, `Optional`, `Callable`, `List`, `Dict`, `Tuple`, etc.) y documentar parámetros y excepciones esperadas.
2. **Tratamiento de Excepciones:**  
   * Nunca capturar `except Exception: pass` genérico en silencio.
   * Manejar de forma individual: `netmiko.exceptions.NetmikoTimeoutException`, `netmiko.exceptions.NetmikoAuthenticationException`, `pypsrp.exceptions.AuthenticationError`, `serial.SerialException`, `TimeoutError`.
   * Transformar cualquier error en un `DiagnosticResult` con severidad `Severity.CRITICAL` y detalle explícito del fallo.
3. **Desacoplamiento Absoluto de Plugins:**  
   * Un plugin nunca importa código de `ui/`. Solo interactúa con `core.interfaces` y `plugins.base_plugin`.
   * La UI nunca instancia clientes de red directamente; solicita ejecuciones al `PluginManager` vía colas (`Queue`).
4. **Timeouts Obligatorios:** Toda conexión de red debe tener un timeout explícito (default de conexión: 5.0s, default de lectura: 10.0s) para garantizar que el diagnóstico total finalice en **<25 segundos**.
5. **Compatibilidad con Read-Only FS:** Ningún script debe intentar escribir en la raíz `/` o en su propio directorio local de código `/rei`. Si se requiere crear archivos temporales, usar exclusivamente `/tmp/` o la ruta persistente `/data/`.
6. **Seguridad de Comandos de Sistema y Pruebas:** Prohibido invocar comandos destructivos reales (`poweroff`, `reboot`, `shutdown`) en el entorno de pruebas del host de desarrollo (EndeavourOS, etc.). Toda prueba o ejecución simulada debe utilizar aislamiento con `REI_MOCK_POWER=1`, `REI_DRY_RUN=1` o mocks de `unittest.mock`.
7. **Gestión Sincronizada de Dependencias y Configuración del SO (`install.sh` / `requirements.txt`):**  
   * **Regla de Oro de Instalación:** Cada vez que se introduzca una nueva dependencia (de sistema vía `apt` o de Python vía `pip`) o una nueva propiedad/función a nivel de sistema operativo (servicios systemd, reglas udev, módulos del kernel, configuración de interfaces de red, dnsmasq, permisos o carpetas del sistema):
     1. **Python:** Debe registrarse inmediatamente en `requirements.txt` fijando su versión mínima recomendada y documentarse en la Sección 3.2.
     2. **Sistema (APT) & Configuración de SO:** Debe incorporarse a la rutina de instalación de `install.sh` dentro del bloque correspondiente (paquetes `apt`, módulos de kernel, configuración de hardware o servicios systemd).
     3. **Rollback / Desinstalación:** Es obligatorio actualizar la rutina de reversión (`do_uninstall` / `--uninstall`) en `install.sh` para garantizar que cualquier nuevo servicio, archivo de configuración o recurso del sistema pueda ser deshabilitado y eliminado limpiamente sin dejar residuos.

---

## 11. GUÍA RÁPIDA DE IMPLEMENTACIÓN POR ARCHIVO

### `main.py`
* Inicializa `settings.json`, valida la integridad de `/data/` y `/tmp/`.
* Instancia `ScreenManager` (UI) y `PluginManager` (Core).
* Configura `signal.signal(signal.SIGINT, ...)` y `signal.SIGTERM` para apagado limpio de hardware (apagar display OLED, cerrar puertos serie).

### `core/manager.py`
* Escanea dinámicamente `/plugins/network/` y `/plugins/endpoints/` buscando clases derivadas de `BasePlugin`.
* Provee el método `enqueue_task(plugin_id: str, target_params: dict) -> str` (devuelve `task_id`).
* Gestiona el `ThreadPoolExecutor(max_workers=2)` y despacha resultados a `result_queue`.

### `core/interfaces.py`
* Define interfaces de transporte: `NetworkTransport(ABC)`, `SerialTransportWrapper`, `SSHTransportWrapper`, `WinRMTransportWrapper`, `SNMPTransportWrapper`.
* Implementa métodos comunes: `connect()`, `execute_command()`, `disconnect()`, `health_check()`.

### `core/parser.py`
* `clean_ansi_codes(raw_text: str) -> str`: Elimina secuencias de escape ANSI VT100/VT220.
* `parse_cisco_interfaces(output: str) -> List[dict]`: Generador de interfaces con contadores de drops/CRC.
* `parse_smartctl_output(output: str) -> dict`: Extractor de atributos SMART críticos (Reallocated Sectors, Wear Leveling, Media Errors).

### `ui/display.py`
* `ScreenManager`: Pila de vistas (`view_stack`).
* Clases de vistas: `HeroCardDeckView`, `DetailMetricView`, `ExecutionProgressView`, `QRCodeReportView`.
* Bucle de renderizado continuo a 30 FPS con buffer doble.

### `ui/input_handler.py`
* Mapeo de pines GPIO a eventos: `JOY_UP`, `JOY_DOWN`, `JOY_LEFT`, `JOY_RIGHT`, `JOY_PRESS`, `KEY1`, `KEY2`, `KEY3`.
* Debounce por software (200ms) para evitar rebotes físicos del joystick.

### `plugins/base_plugin.py`
* Clase abstracta `BasePlugin(ABC)` con:
  * `name: str`
  * `category: str` (Network / Endpoint)
  * `supported_interfaces: List[str]` (Serial, SSH, WinRM, SNMP)
  * `@abstractmethod def run_diagnostic(self, transport: BaseTransport, **kwargs) -> DiagnosticResult`

### `install.sh`
* Automatiza la instalación de paquetes del sistema (`apt`), módulos de hardware (I2C/SPI) y librerías Python (`requirements.txt`).
* Despliega y habilita el servicio systemd (`/etc/systemd/system/rei.service`) para autoinicio en el arranque como superusuario (`root`).
* Provee interfaz de terminal centrada con ASCII Art y menú interactivo o flags (`--install`, `--uninstall` / `--revert`, `--help`).
* Provee la rutina de reversión completa (`do_uninstall`) para restaurar el estado del sistema sin alterar datos locales ni código fuente.