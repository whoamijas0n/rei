# REI (Portable Multi-Interface Diagnostic Hub)

REI es un dispositivo de campo de bolsillo autónomo para diagnóstico rápido de infraestructura TI y endpoints, diseñado para Raspberry Pi Zero 2 W con pantalla OLED SH1106 de 1.3" (128x64 px).

## Características Principales

- **Arquitectura Asíncrona:** UI fija a 30 FPS desacoplada del pool de workers diagnósticos (`ThreadPoolExecutor`).
- **Minimalist Hero Card System:** Navegación por tarjetas hero centradas (20x20 px) con paginación por micro-puntos e indicadores perimetrales.
- **Manejo de Hardware:** Soporte para Waveshare 1.3" OLED HAT (SPI/I2C), joystick de 5 vías y 3 botones físicos con `gpiozero`.
- **Monolito Modular:** Plugins desacoplados con contratos estrictos (`IDiagnosticPlugin`).
- **Alimentación y Utilidades:** Control seguro de apagado (`poweroff`) y reinicio (`reboot`) del sistema operativo con sincronización de almacenamiento previa.
- **Contraseña Wi-Fi por Teclado USB:** Las redes protegidas se escriben con un teclado USB físico (`WiFiPasswordView`); la contraseña se puede mostrar/ocultar con `KEY2`. El teclado virtual en OLED (`VirtualKeyboardInputView`) se conserva por compatibilidad pero ya no se usa en el flujo Wi-Fi.

## Conexión Wi-Fi con Teclado USB

Al seleccionar una red protegida, la OLED muestra `CONECTE TECLADO USB` / `INGRESE LA CLAVE` y espera un teclado físico:

- **Requisito de hardware:** la Pi Zero 2 W tiene un único puerto USB OTG. Para que detecte un teclado debe estar en **`UTILIDADES → MODO USB → MODO USB NORMAL`** (modo host) y luego reiniciar. En modo HID (gadget/periférico) el puerto no puede leer teclados; la pantalla lo indica con `MODO HID ACTIVO / USE MODO NORMAL`.
- **Conexión del teclado:** adaptador OTG al puerto USB de datos; la alimentación va por el puerto PWR.
- **Escritura:** teclee la contraseña; `Backspace`/`Delete` borra.
- **`ENTER` / `KEY1` / `PRESS`:** validar y conectar (mínimo 8 caracteres en WPA).
- **`KEY2`:** mostrar u ocultar la contraseña (empieza siempre enmascarada).
- **`KEY3` / `Esc`:** cancelar y volver a la lista de redes.
- El teclado se detecta en caliente (≤2 s) y se descartan falsos teclados (p. ej. `vc4-hdmi`).

### Distribución del teclado

El layout se resuelve en este orden (ver `ui/keyboard_layouts.py`):

1. Variable de entorno `REI_KEYBOARD_LAYOUT` (`us` / `es` / `latam`).
2. `keyboard.layout` en `config/settings.json` (`"auto"` por defecto).
3. `XKBLAYOUT` de `/etc/default/keyboard`.
4. `KEYMAP` de `/etc/vconsole.conf`.
5. `us` como último recurso.

Soporta US, ES y LatAm, incluyendo `AltGr` y teclas muertas (`´`, `` ` ``, `^`, `¨`, `~`).

### Conflicto USB host/gadget

El modo teclado USB (host) y el modo inyección HID (`/dev/hidg0`) **no pueden coexistir**: comparten el único controlador OTG. Cambie de modo según la tarea y reinicie; la app no lo hace automáticamente.


## Jerarquía de Navegación

- **Nivel 0 (Menú Principal):**
  - `UTILIDADES` (Icono: TOOLS / Gear)
  - `SWITCHES / RED` (Icono: NETWORK)
  - `ENDPOINTS PC` (Icono: ENDPOINT)
  - `BOVEDA / VAULT` (Icono: VAULT)

- **Nivel 1 (`UTILIDADES`):**
  - `CONEXION DE RED` -> `VER DIRECCION IP`, `ESCANEAR WI-FI`
  - `ESTADO BATERIA` -> Telemetría de batería y voltaje
  - `ESTADO SISTEMA` -> CPU, RAM, temperatura y kernel
  - `ALIMENTACION` -> Submenú de control de energía

- **Nivel 2 (`ALIMENTACION`):**
  - `APAGAR` (Icono: POWEROFF) -> Ejecuta `poweroff` seguro del sistema.
  - `REINICIAR` (Icono: REBOOT) -> Ejecuta `reboot` seguro del sistema.

## Instalación y Autoinicio (Systemd)

Para instalar dependencias del sistema, paquetes de Python y configurar el autoinicio como superusuario:

```bash
# Menú interactivo centrado
sudo ./install.sh

# O instalación directa vía CLI
sudo ./install.sh --install

# Revertir todos los cambios y desinstalar el servicio
sudo ./install.sh --uninstall
```

> **⚠ Reinicia después de instalar.** El firmware de la Raspberry Pi solo lee
> `config.txt` durante el arranque. `install.sh` habilita ahí SPI (`/dev/spidev0.0`,
> para el OLED y el splash), I2C (`/dev/i2c-1`, para el PiSugar) y el USB OTG en
> modo peripheral (`/dev/hidg0`, para la inyección HID). Sin un reinicio esos
> dispositivos no existen y la pantalla queda en blanco.
>
> ```bash
> sudo reboot
> ```
>
> `install.sh` te avisa de esto al terminar.

## Ejecución Manual

```bash
# Ejecución en entorno de desarrollo / pruebas (sin disparar apagado real)
REI_DRY_RUN=1 python3 main.py
```

Antes de la primera ejecución hay que crear la configuración real (no está versionada):

```bash
cp config/settings.example.json config/settings.json
```

La clave de Gemini (`gemini.api_key`) debe ir en `config/settings.json`; la variable de
entorno `GEMINI_API_KEY` es ignorada por `main.py`.

## Pruebas

```bash
python3 -m unittest discover -s tests -v
```

> `tests/test_ui.py` requiere `Pillow` instalado; el resto de la suite no.
> `tests/test_wifi_password_view.py` se omite automáticamente sin Pillow.
