from PIL import ImageDraw, ImageFont, Image
import time
from typing import Optional, Callable
import queue
from ui.icons import get_icon_system, get_icon_network, get_icon_endpoints, get_icon_settings
from luma.core.render import canvas
import math

try:
    # Try to load a nice font, fallback to default
    FONT_DEFAULT = ImageFont.load_default()
    FONT_TITLE = ImageFont.load_default()
except Exception:
    FONT_DEFAULT = ImageFont.load_default()
    FONT_TITLE = ImageFont.load_default()

class View:
    def handle_input(self, action: str):
        pass

    def render(self, draw: ImageDraw.ImageDraw, sys_data: dict):
        pass

class SystemInfoView(View):
    def __init__(self, manager: 'UIManager', on_update: Callable):
        self.manager = manager
        self.on_update = on_update
        self.selected_idx = 0
        self.scroll_offset = 0
        self.line_height = 12
        self.max_visible = 4

    def handle_input(self, action: str):
        if action == "KEY3":
            self.manager.set_view(self.manager.main_menu)
        elif action == "DOWN":
            self.selected_idx += 1
        elif action == "UP":
            self.selected_idx -= 1
        elif action in ["PRESS", "KEY1"]:
            if self.selected_idx == 7: # El index de "Actualizar"
                self.on_update()

    def render(self, draw: ImageDraw.ImageDraw, sys_data: dict):
        if not sys_data:
            draw.text((5, 25), "Cargando datos...", font=FONT_DEFAULT, fill="white")
            return

        is_updating = sys_data.get('is_updating', False)
        
        # Generar las lineas
        lines = [
            f"Bat: {sys_data['battery']['percent']}% ({sys_data['battery']['voltage']}V)",
            f"WiFi: {sys_data['wifi']['ssid']}",
            f"IP: {sys_data['wifi']['ip']}",
            f"Temp: {sys_data['health']['temp']}",
            f"Uptime: {sys_data['health']['uptime']}",
            f"Load: {sys_data['health']['load']}",
            f"Ver: {sys_data['version']}",
            f"> ACTUALIZAR SISTEMA" if not is_updating else "> ACTUALIZANDO..."
        ]

        # Limitar cursor
        self.selected_idx = max(0, min(self.selected_idx, len(lines) - 1))

        # Ajustar scroll
        if self.selected_idx < self.scroll_offset:
            self.scroll_offset = self.selected_idx
        elif self.selected_idx >= self.scroll_offset + self.max_visible:
            self.scroll_offset = self.selected_idx - self.max_visible + 1

        y_offset = 5
        for i in range(self.scroll_offset, min(len(lines), self.scroll_offset + self.max_visible)):
            y = y_offset + (i - self.scroll_offset) * self.line_height
            text = lines[i]
            
            if i == self.selected_idx:
                # Invertir colores para la seleccion
                bbox = draw.textbbox((4, y), text, font=FONT_DEFAULT)
                draw.rectangle((3, y, 120, y + self.line_height), fill="white")
                draw.text((4, y), text, font=FONT_DEFAULT, fill="black")
            else:
                draw.text((4, y), text, font=FONT_DEFAULT, fill="white")

        # Spinner si esta actualizando
        if is_updating:
            spinner_chars = ['|', '/', '-', '\\']
            idx = int(time.time() * 4) % 4
            draw.text((115, 50), spinner_chars[idx], font=FONT_DEFAULT, fill="white")

        # Scrollbar simple
        if len(lines) > self.max_visible:
            sb_h = int((self.max_visible / len(lines)) * 50)
            sb_y = 5 + int((self.scroll_offset / len(lines)) * 50)
            draw.rectangle((123, sb_y, 124, sb_y + sb_h), fill="white")

class HeroCardDeckView(View):
    def __init__(self, manager: 'UIManager'):
        self.manager = manager
        self.cards = [
            {"title": "INFO SISTEMA", "icon": get_icon_system(), "view": manager.system_info},
            {"title": "REDES", "icon": get_icon_network(), "view": None},
            {"title": "ENDPOINTS", "icon": get_icon_endpoints(), "view": None},
            {"title": "AJUSTES", "icon": get_icon_settings(), "view": None}
        ]
        self.current_idx = 0
        self.show_stub = False
        self.stub_timer = 0

    def handle_input(self, action: str):
        if self.show_stub:
            if action in ["KEY3", "LEFT", "RIGHT", "PRESS", "KEY1"]:
                self.show_stub = False
            return

        if action == "RIGHT":
            self.current_idx = (self.current_idx + 1) % len(self.cards)
        elif action == "LEFT":
            self.current_idx = (self.current_idx - 1) % len(self.cards)
        elif action in ["PRESS", "KEY1"]:
            target_view = self.cards[self.current_idx]["view"]
            if target_view:
                self.manager.set_view(target_view)
            else:
                self.show_stub = True
                self.stub_timer = time.time()

    def render(self, draw: ImageDraw.ImageDraw, sys_data: dict):
        if self.show_stub:
            msg = "Modulo en\ndesarrollo"
            bbox = draw.multiline_textbbox((0, 0), msg, font=FONT_DEFAULT, align="center")
            w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
            draw.multiline_text(((128 - w) // 2, (64 - h) // 2), msg, font=FONT_DEFAULT, fill="white", align="center")
            
            if time.time() - self.stub_timer > 2:
                self.show_stub = False
            return

        card = self.cards[self.current_idx]
        
        # Paginacion (Dots)
        dot_w = 4
        dot_spacing = 2
        total_w = len(self.cards) * dot_w + (len(self.cards) - 1) * dot_spacing
        start_x = 126 - total_w - 2
        start_y = 3
        
        for i in range(len(self.cards)):
            x = start_x + i * (dot_w + dot_spacing)
            if i == self.current_idx:
                draw.rectangle((x, start_y, x + dot_w - 1, start_y + dot_w - 1), fill="white")
            else:
                draw.rectangle((x, start_y, x + dot_w - 1, start_y + dot_w - 1), outline="white")

        # Icono (centrado en X=64, Y=24, tamano 20x20 -> topLeft = 54, 14)
        icon_x = 54
        icon_y = 14
        draw.bitmap((icon_x, icon_y), card["icon"], fill="white")

        # Titulo
        bbox = draw.textbbox((0, 0), card["title"], font=FONT_TITLE)
        w = bbox[2] - bbox[0]
        draw.text(((128 - w) // 2, 44), card["title"], font=FONT_TITLE, fill="white")

class UIManager:
    def __init__(self, device, data_queue: queue.Queue, on_update_requested: Callable):
        self.device = device
        self.data_queue = data_queue
        self.latest_sys_data = {}
        
        # Inicializar vistas
        self.system_info = SystemInfoView(self, on_update_requested)
        self.main_menu = HeroCardDeckView(self)
        
        self.current_view: View = self.main_menu

    def set_view(self, view: View):
        self.current_view = view

    def handle_input(self, action: str):
        if self.current_view:
            self.current_view.handle_input(action)

    def update_and_render(self):
        # Drenar cola para tener el ultimo dato
        while not self.data_queue.empty():
            try:
                self.latest_sys_data = self.data_queue.get_nowait()
            except queue.Empty:
                break

        with canvas(self.device) as draw:
            # Borde Perimetral Continuo
            draw.rectangle((1, 1, 126, 62), outline="white", fill="black")
            
            if self.current_view:
                self.current_view.render(draw, self.latest_sys_data)
