"""
REI - Early Boot Splash Screen
Ultra-fast, standalone 1-bit monochrome boot splash for 1.3" SH1106 OLED (128x64 px).
Initializes hardware via SPI (DC=GPIO24, RST=GPIO25, rotate=2) with I2C fallback,
renders the static centered 'REI' frame, transfers buffer to display GDDRAM with persist=True,
and exits cleanly without resetting GPIO pins or clearing display memory.

Note: /dev/spidev0.0 only exists if the Pi booted with dtparam=spi=on in
config.txt (the firmware is read at boot time, not at runtime).
"""

import logging
import os
import sys
import time
from PIL import Image, ImageDraw

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] REI.Splash: %(message)s")
logger = logging.getLogger("REI.Splash")

# Nodos de dispositivo que debe exponer el kernel para poder hablar con el panel.
# El firmware de la Pi solo aplica dtparam=spi=on / dtparam=i2c_arm=on al
# arrancar, así que el nodo puede tardar un instante en aparecer tras el boot.
SPI_DEVICE_PATH = "/dev/spidev0.0"
I2C_DEVICE_PATH = "/dev/i2c-1"


def wait_for_device(path: str, timeout: float = 5.0) -> bool:
    """Espera a que el kernel/udeg creen el nodo de dispositivo indicado."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(path):
            return True
        time.sleep(0.1)
    return False


def close_interface(serial_interface) -> None:
    """
    Cierra el bus SPI/I2C sin resetear los pines GPIO (RST/DC).

    luma llama a cleanup() al liberar el dispositivo: en el SPI eso hace
    self._spi.close() y, solo si _managed es True, resetea RST/DC (lo que
    borraría la imagen). Con _managed=False el cierre del bus es seguro y el
    contenido permanece en la RAM del panel (device.persist = True).
    """
    if serial_interface is None:
        return
    try:
        serial_interface.cleanup()
    except Exception as cleanup_err:  # pragma: no cover - best effort
        logger.debug(f"Interfaz de display: cierre sin efecto ({cleanup_err}).")


def render_splash_frame(width: int = 128, height: int = 64) -> Image.Image:
    """
    Renders static 1-bit monochrome boot splash frame:
    - 1px continuous perimeter border: (1, 1) to (126, 62)
    - Bold centered pixel-art 'REI' (width=50px, height=24px, centered at cx=64, cy=32)
    - Zero progress bars, percentages, or CPU-intensive animations.
    """
    img = Image.new("1", (width, height), "black")
    draw = ImageDraw.Draw(img)

    # 1. Perimeter Border (1, 1) to (126, 62)
    draw.rectangle((1, 1, 126, 62), outline="white", fill="black")

    # 2. Centered Pixel-Art "REI" (width=50, height=24)
    # Target center: cx=64, cy=32
    # Geometry:
    # Letter R: x: 39..53, y: 20..43 (width 15, height 24)
    # Left vertical stem (3px)
    draw.rectangle((39, 20, 42, 43), fill="white")
    # Top horizontal bar (3px)
    draw.rectangle((39, 20, 51, 23), fill="white")
    # Right upper vertical curve/stem (3px)
    draw.rectangle((49, 20, 52, 31), fill="white")
    # Middle horizontal bar (3px)
    draw.rectangle((39, 29, 51, 32), fill="white")
    # Diagonal leg (4px stroke)
    draw.polygon([(45, 32), (49, 32), (53, 43), (49, 43)], fill="white")

    # Letter E: x: 57..70, y: 20..43 (width 14, height 24)
    # Left vertical stem (3px)
    draw.rectangle((57, 20, 60, 43), fill="white")
    # Top horizontal bar (3px)
    draw.rectangle((57, 20, 70, 23), fill="white")
    # Middle horizontal bar (3px)
    draw.rectangle((57, 30, 67, 33), fill="white")
    # Bottom horizontal bar (3px)
    draw.rectangle((57, 40, 70, 43), fill="white")

    # Letter I: x: 75..88, y: 20..43 (width 14, height 24)
    # Top horizontal bar (3px)
    draw.rectangle((75, 20, 87, 23), fill="white")
    # Center vertical stem (4px)
    draw.rectangle((79, 20, 83, 43), fill="white")
    # Bottom horizontal bar (3px)
    draw.rectangle((75, 40, 87, 43), fill="white")

    return img


def display_splash(max_retries: int = 10, retry_interval: float = 0.5) -> bool:
    """
    Initializes display device, transfers buffer to OLED RAM, and releases bus.
    Enables persist=True so that SH1106 display RAM is preserved on script exit.
    Returns True if successfully sent to physical display, False otherwise.

    Los fallos se registran a WARNING (no DEBUG) para que el journal del
    servicio diga siempre POR QUÉ no se pudo escribir en el panel.
    """
    buffer = render_splash_frame(128, 64)

    for attempt in range(1, max_retries + 1):
        try:
            from luma.core.interface.serial import i2c, spi
            from luma.oled.device import sh1106

            # El nodo SPI lo crea el kernel al arrancar; puede tardar unos ms.
            if attempt == 1 and not wait_for_device(SPI_DEVICE_PATH, timeout=5.0):
                logger.warning(
                    f"{SPI_DEVICE_PATH} no disponible todavía "
                    "(¿dtparam=spi=on en config.txt y reinicio?)."
                )

            # 1. SPI Interface (Waveshare 1.3" OLED HAT: DC=GPIO24, RST=GPIO25, rotate=2)
            serial_interface = None
            try:
                serial_interface = spi(
                    device=0,
                    port=0,
                    bus_speed_hz=8000000,
                    gpio_DC=24,
                    gpio_RST=25,
                )
                # Prevent serial cleanup from resetting GPIO RST/DC pins on exit
                serial_interface._managed = False

                device = sh1106(serial_interface, width=128, height=64, rotate=2)
                device.persist = True  # Prevent clearing display RAM & turning off OLED on exit
                device.display(buffer)
                logger.info("Splash screen successfully sent to OLED via SPI (persist=True).")
                return True
            except Exception as spi_err:
                logger.warning(f"SPI attempt {attempt}/{max_retries} failed: {spi_err}")
            finally:
                close_interface(serial_interface)

            # 2. I2C Interface Fallback
            serial_interface = None
            try:
                serial_interface = i2c(port=1, address=0x3C)
                device = sh1106(serial_interface, width=128, height=64, rotate=2)
                device.persist = True
                device.display(buffer)
                logger.info("Splash screen successfully sent to OLED via I2C (persist=True).")
                return True
            except Exception as i2c_err:
                logger.warning(f"I2C attempt {attempt}/{max_retries} failed: {i2c_err}")
            finally:
                close_interface(serial_interface)

        except ImportError as imp_err:
            logger.error(
                f"luma.oled dependencies not installed: {imp_err}. "
                "Instala requirements.txt dentro del venv de REI."
            )
            return False
        except Exception as ex:
            logger.warning(f"General display initialization error (attempt {attempt}): {ex}")

        if attempt < max_retries:
            time.sleep(retry_interval)

    logger.error(
        "Splash screen: physical display not ready or unavailable "
        f"(SPI={SPI_DEVICE_PATH}, I2C={I2C_DEVICE_PATH})."
    )
    return False


if __name__ == "__main__":
    display_splash()
    sys.exit(0)
