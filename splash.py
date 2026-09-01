"""
REI - Early Boot Splash Screen
Ultra-fast, standalone 1-bit monochrome boot splash for 1.3" SH1106 OLED (128x64 px).
Initializes hardware via SPI (DC=GPIO24, RST=GPIO25, rotate=2) with I2C fallback,
renders the static centered 'REI' frame, transfers buffer to display GDDRAM, and exits cleanly.
"""

import sys
from PIL import Image, ImageDraw


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


def display_splash() -> bool:
    """
    Initializes display device, transfers buffer to OLED RAM, and releases bus.
    Returns True if successfully sent to physical display, False otherwise.
    """
    buffer = render_splash_frame(128, 64)

    try:
        from luma.core.interface.serial import i2c, spi
        from luma.oled.device import sh1106

        # 1. SPI Interface (Waveshare 1.3" OLED HAT: DC=GPIO24, RST=GPIO25, rotate=2)
        try:
            serial_interface = spi(
                device=0,
                port=0,
                bus_speed_hz=8000000,
                gpio_DC=24,
                gpio_RST=25,
            )
            device = sh1106(serial_interface, width=128, height=64, rotate=2)
            device.display(buffer)
            return True
        except Exception:
            pass

        # 2. I2C Interface Fallback
        try:
            serial_interface = i2c(port=1, address=0x3C)
            device = sh1106(serial_interface, width=128, height=64, rotate=2)
            device.display(buffer)
            return True
        except Exception:
            pass

    except Exception:
        pass

    return False


if __name__ == "__main__":
    display_splash()
    sys.exit(0)
