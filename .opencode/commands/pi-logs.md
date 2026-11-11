---
description: Muestra los logs del servicio REI en la Raspberry Pi
---

!`ssh rei-pi "sudo journalctl -u rei.service -n 40 --no-pager"`

Analiza las líneas del journal de `rei.service` y resume los errores relevantes.
