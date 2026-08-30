import time
import queue
import sys
from luma.core.interface.serial import spi
from luma.oled.device import sh1106
from gpiozero import Button

from core.system_monitor import SystemMonitor
from ui.engine import UIManager

def main():
    # Inicializar SPI y display SH1106
    try:
        serial = spi(device=0, port=0, bus_speed_hz=8000000, gpio_DC=24, gpio_RST=25)
        device = sh1106(serial, rotate=0)
    except Exception as e:
        print(f"Error inicializando display: {e}")
        print("Asegurate de ejecutar esto en la Raspberry Pi con SPI habilitado.")
        sys.exit(1)

    # Cola (Queue) para el patron Productor-Consumidor
    data_queue = queue.Queue()

    # Inicializar Monitor del Sistema (Productor en background)
    monitor = SystemMonitor(data_queue)
    monitor.start()

    # Callback para la accion de Actualizar Sistema
    def request_update():
        monitor.start_system_update()

    # Inicializar el Motor de UI (Consumidor)
    ui_manager = UIManager(device, data_queue, request_update)

    # Inicializar GPIOs
    # Joystick: UP=6, DOWN=19, LEFT=5, RIGHT=26, PRESS=13
    # Botones: KEY1=21, KEY2=16, KEY3=20
    try:
        btn_up = Button(6, pull_up=True, bounce_time=0.1)
        btn_down = Button(19, pull_up=True, bounce_time=0.1)
        btn_left = Button(5, pull_up=True, bounce_time=0.1)
        btn_right = Button(26, pull_up=True, bounce_time=0.1)
        btn_press = Button(13, pull_up=True, bounce_time=0.1)
        
        key1 = Button(21, pull_up=True, bounce_time=0.1)
        key2 = Button(16, pull_up=True, bounce_time=0.1)
        key3 = Button(20, pull_up=True, bounce_time=0.1)

        # Mapear eventos a la UI
        btn_up.when_pressed = lambda: ui_manager.handle_input("UP")
        btn_down.when_pressed = lambda: ui_manager.handle_input("DOWN")
        btn_left.when_pressed = lambda: ui_manager.handle_input("LEFT")
        btn_right.when_pressed = lambda: ui_manager.handle_input("RIGHT")
        btn_press.when_pressed = lambda: ui_manager.handle_input("PRESS")
        
        key1.when_pressed = lambda: ui_manager.handle_input("KEY1")
        key2.when_pressed = lambda: ui_manager.handle_input("KEY2")
        key3.when_pressed = lambda: ui_manager.handle_input("KEY3")
        
    except Exception as e:
        print(f"Error inicializando GPIOs (Quiza estas en PC?): {e}")

    print("OmniDiag Hub Iniciado. Presiona Ctrl+C para salir.")
    
    # Bucle principal a 30 FPS fijos (Render Loop)
    try:
        while True:
            start_t = time.time()
            
            ui_manager.update_and_render()
            
            # Control estricto de FPS
            elapsed = time.time() - start_t
            sleep_time = (1.0 / 30.0) - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)
                
    except KeyboardInterrupt:
        print("\nDeteniendo sistema...")
    finally:
        monitor.stop()
        device.cleanup()
        sys.exit(0)

if __name__ == '__main__':
    main()
