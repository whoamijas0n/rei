"""
Preview generator: Renders snapshots of all views and cards to verify visual aesthetics.
"""

import os
from PIL import Image, ImageDraw

from ui.display import HeroCardDeckView, DetailCardView, HeroCard, IconRenderer
from core.plugins import IPAddressPlugin, BatteryStatusPlugin

os.makedirs("artifacts_preview", exist_ok=True)

# 1. Preview Root Deck - Card 0 (INFO SISTEMA)
root_deck = HeroCardDeckView("MAIN")
root_deck.add_card(HeroCard(title="INFO SISTEMA", icon_name="INFO"))
root_deck.add_card(HeroCard(title="SWITCHES / RED", icon_name="NETWORK"))
root_deck.add_card(HeroCard(title="ENDPOINTS PC", icon_name="ENDPOINT"))
root_deck.add_card(HeroCard(title="BOVEDA / VAULT", icon_name="VAULT"))

img_card0 = Image.new("1", (128, 64), 0)
draw0 = ImageDraw.Draw(img_card0)
root_deck.render(draw0, 128, 64)
img_card0.convert("RGB").save("artifacts_preview/preview_card0_info.png")

# 2. Preview Root Deck - Card 1 (SWITCHES / RED)
root_deck.active_index = 1
img_card1 = Image.new("1", (128, 64), 0)
draw1 = ImageDraw.Draw(img_card1)
root_deck.render(draw1, 128, 64)
img_card1.convert("RGB").save("artifacts_preview/preview_card1_switches.png")

# 3. Preview Root Deck - Card 2 (ENDPOINTS PC)
root_deck.active_index = 2
img_card2 = Image.new("1", (128, 64), 0)
draw2 = ImageDraw.Draw(img_card2)
root_deck.render(draw2, 128, 64)
img_card2.convert("RGB").save("artifacts_preview/preview_card2_endpoints.png")

# 4. Preview Root Deck - Card 3 (BOVEDA / VAULT)
root_deck.active_index = 3
img_card3 = Image.new("1", (128, 64), 0)
draw3 = ImageDraw.Draw(img_card3)
root_deck.render(draw3, 128, 64)
img_card3.convert("RGB").save("artifacts_preview/preview_card3_vault.png")

# 5. Preview Detail View (DIRECCION IP)
detail = DetailCardView("DIRECCION IP")
ip_plug = IPAddressPlugin()
res = ip_plug.run()
detail.set_content(lines=res.details, status="OK", is_loading=False)

img_detail = Image.new("1", (128, 64), 0)
draw_detail = ImageDraw.Draw(img_detail)
detail.render(draw_detail, 128, 64)
img_detail.convert("RGB").save("artifacts_preview/preview_detail_ip.png")

# 6. Preview Detail View (ESTADO BATERIA)
detail_bat = DetailCardView("ESTADO BATERIA")
bat_plug = BatteryStatusPlugin()
res_bat = bat_plug.run()
detail_bat.set_content(lines=res_bat.details, status="OK", is_loading=False)

img_detail_bat = Image.new("1", (128, 64), 0)
draw_detail_bat = ImageDraw.Draw(img_detail_bat)
detail_bat.render(draw_detail_bat, 128, 64)
img_detail_bat.convert("RGB").save("artifacts_preview/preview_detail_battery.png")

print("All preview snapshots rendered successfully to artifacts_preview/")
