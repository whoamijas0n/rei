---
description: Ejecuta un comando en la Raspberry Pi y analiza su salida
---

Ejecuta este comando en la Raspberry Pi (alias SSH `rei-pi`) y analiza la salida.
El servicio REI corre como `rei.service`; usa `sudo journalctl -u rei.service` para los logs.

!`ssh rei-pi "$ARGUMENTS"`
