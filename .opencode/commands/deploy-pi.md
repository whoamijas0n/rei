---
description: Despliega el código local a la Raspberry Pi, ejecuta los tests y reinicia el servicio
---

Despliega el estado actual del repositorio a la Raspberry Pi (alias SSH `rei-pi`),
ejecuta la suite de tests en la placa y reinicia `rei.service`.

!`tools/deploy_pi.sh --tests`

Si el script falla, analiza la causa (tests fallidos, error de SSH, venv) antes de
reintentar. No borres ni modifiques `config/settings.json` ni el `.venv` de la Pi.
