# REI (Portable Multi-Interface Diagnostic Hub)

REI es un dispositivo de campo de bolsillo autónomo para diagnóstico rápido de infraestructura TI y endpoints, diseñado para Raspberry Pi Zero 2 W con pantalla OLED SH1106 de 1.3" (128x64 px).

## Características Principales

- **Arquitectura Asíncrona:** UI fija a 30 FPS desacoplada del pool de workers diagnósticos (`ThreadPoolExecutor`).
- **Minimalist Hero Card System:** Navegación por tarjetas hero centradas (20x20 px) con paginación por micro-puntos e indicadores perimetrales.
- **Manejo de Hardware:** Soporte para Waveshare 1.3" OLED HAT (SPI/I2C), joystick de 5 vías y 3 botones físicos con `gpiozero`.
- **Monolito Modular:** Plugins desacoplados con contratos estrictos (`IDiagnosticPlugin`).
- **Alimentación y Utilidades:** Control seguro de apagado (`poweroff`) y reinicio (`reboot`) del sistema operativo con sincronización de almacenamiento previa.
- **Teclado Virtual Interactivo (Virtual Keyboard):** Ingreso autónomo de contraseñas Wi-Fi sin necesidad de teclados USB físicos, operado 100% mediante el Joystick y los botones físicos del HAT.

## Operación del Teclado Virtual (Wi-Fi Authentication)

Al conectarse a una red protegida por contraseña, se despliega automáticamente el **Teclado Virtual** en la pantalla OLED (128x64 px):

- **Controles del Joystick:**
  - **`UP` / `DOWN` / `LEFT` / `RIGHT`:** Navegar por la cuadrícula 4x10 de caracteres y teclas funcionales.
  - **`PRESS` (Centro):** Escribir el carácter enfocado o accionar la tecla funcional (`ABC`/`abc`, `123`, `SYM`, `SP`, `DEL`, `OK`).
- **Atajos con Botones Físicos:**
  - **`KEY1`:** **Confirmar / Enviar** contraseña e iniciar la conexión inmediatamente.
  - **`KEY2`:** **Borrar** (Backspace) el último carácter ingresado.
  - **`KEY3`:** **Cancelar y Volver** a la lista de redes escaneadas.
- **Capas de Caracteres Disponibles:**
  - **`abc` (Minúsculas):** Caracteres `a-z`, `@`, `.`, `_`, `-`, espacio, borrar, confirmación y cambio de capa.
  - **`ABC` (Mayúsculas):** Caracteres `A-Z`, `@`, `.`, `_`, `-`, etc.
  - **`123` (Números y Símbolos Básicos):** Dígitos `0-9`, `! @ # $ % ^ & * ( ) - _ + = [ ] { } ; :`.
  - **`SYM` (Símbolos Extendidos):** Símbolos `~ \` | \\ / ? < > , . ' " $ % ^ & * + = # ! @ : ; ( ) [ ] { }`.


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

## Ejecución Manual

# Ejecución en entorno de desarrollo / pruebas (sin disparar apagado real)
REI_DRY_RUN=1 python3 main.py
```

## Pruebas

```bash
python3 -m unittest discover -s tests -v
```