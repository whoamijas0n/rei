"""
REI - Capa de Transporte Serial y Detección Pasiva para Switches (core/serial_console.py)

Proporciona descubrimiento multi-puerto (/dev/ttyUSB* para cables consola USB->RJ45
y /dev/ttyACM* para cables USB Mini-B/Micro-B/USB-C directos), auto-detección segura
de baudrate enviando únicamente saltos de línea (sin comandos ni credenciales),
clasificación de prompts y huella digital pasiva del fabricante.

Soporta simulación segura mediante REI_DRY_RUN=1 o REI_MOCK_SERIAL=1.
"""

from dataclasses import dataclass, field
from enum import Enum
import logging
import os
import re
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger("REI.Core.SerialConsole")

try:
    import serial
    from serial import SerialException
    from serial.tools import list_ports

    PYSERIAL_AVAILABLE = True
except ImportError:
    serial = None  # type: ignore
    SerialException = OSError  # type: ignore
    list_ports = None  # type: ignore
    PYSERIAL_AVAILABLE = False


DEFAULT_BAUDRATES: Tuple[int, ...] = (9600, 115200, 38400)

# Secuencias ANSI/VT100 que algunos switches envían en banners o prompts
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[@-Z\\-_]")

# Chips USB-Serial conocidos para priorizar el puerto correcto en sistemas con varios dispositivos
_PREFERRED_CHIP_KEYWORDS: Tuple[str, ...] = (
    "ftdi",
    "ft232",
    "cp210",
    "silicon labs",
    "cisco",
    "prolific",
    "pl2303",
    "ch340",
    "usb serial",
    "console",
)

# Patrones de clasificación de estado de prompt (de más específico a más general)
_INITIAL_DIALOG_RE = re.compile(
    r"(initial\s+configuration\s+dialog|continue\s+with\s+configuration\s+dialog).*?\[yes/no\]",
    re.IGNORECASE | re.DOTALL,
)
_PRESS_RETURN_RE = re.compile(
    r"(press\s+return\s+to\s+get\s+started|press\s+any\s+key\s+to\s+continue|hit\s+return)",
    re.IGNORECASE,
)
_TRANSLATING_RE = re.compile(
    r"(translating\s+\"|domain\s+server\s*\(|%\s*unknown\s+command\s+or\s+computer\s+name)",
    re.IGNORECASE,
)
_LOGIN_USER_RE = re.compile(
    r"(?:^|\n)\s*(?:username|user\s*name|login|user)\s*:\s*$",
    re.IGNORECASE,
)
_LOGIN_PASS_RE = re.compile(
    r"(?:^|\n)\s*(?:password|passcode|enable\s+password)\s*:\s*$",
    re.IGNORECASE,
)
_PRIV_PROMPT_RE = re.compile(
    r"^[A-Za-z0-9._:@/\-\(\)]{1,48}#\s*$"
)
_USER_PROMPT_RE = re.compile(
    r"^(?:\[[^\]]+@[^\]]+\]\s*>|[A-Za-z0-9._:@/\-\(\)]{1,48}>|<[A-Za-z0-9._\-]{1,48}>)\s*$"
)
_HUAWEI_SYS_PROMPT_RE = re.compile(
    r"^\[[A-Za-z0-9._\-]{1,48}\]\s*$"
)

# Huellas pasivas de fabricantes visibles en banner/prompt (sin enviar comandos)
_PASSIVE_VENDOR_RULES: Tuple[Tuple[str, Tuple[re.Pattern, ...]], ...] = (
    (
        "cisco_ios",
        (
            re.compile(r"\bcisco\b", re.IGNORECASE),
            re.compile(r"\bcatalyst\b", re.IGNORECASE),
            re.compile(r"\bws-c\d{4}", re.IGNORECASE),
            re.compile(r"\bc\d{4}[a-z]?\b", re.IGNORECASE),
            re.compile(r"\buser\s+access\s+verification\b", re.IGNORECASE),
            re.compile(r"\bpress\s+return\s+to\s+get\s+started\b", re.IGNORECASE),
            re.compile(r"\bios\s+software\b", re.IGNORECASE),
            re.compile(r"\bcon0\s+is\s+now\s+available\b", re.IGNORECASE),
        ),
    ),
    (
        "tplink_jetstream",
        (
            re.compile(r"\btp-?link\b", re.IGNORECASE),
            re.compile(r"\bjetstream\b", re.IGNORECASE),
            re.compile(r"\btl-s[gx]\d+", re.IGNORECASE),
            re.compile(r"\bomada\b", re.IGNORECASE),
        ),
    ),
    (
        "aruba_cx",
        (
            re.compile(r"\barubaos-?cx\b", re.IGNORECASE),
            re.compile(r"\baos-?cx\b", re.IGNORECASE),
        ),
    ),
    (
        "hp_procurve",
        (
            re.compile(r"\bprocurve\b", re.IGNORECASE),
            re.compile(r"\bhewlett-?packard\b", re.IGNORECASE),
            re.compile(r"\baruba\b", re.IGNORECASE),
            re.compile(r"\bhp\s+j\d{4}", re.IGNORECASE),
        ),
    ),
    (
        "mikrotik",
        (
            re.compile(r"\bmikrotik\b", re.IGNORECASE),
            re.compile(r"\brouteros\b", re.IGNORECASE),
            re.compile(r"\[[^\]]+@[^\]]+\]\s*>"),
        ),
    ),
    (
        "huawei",
        (
            re.compile(r"\bhuawei\b", re.IGNORECASE),
            re.compile(r"\bvrp\b", re.IGNORECASE),
            re.compile(r"\bquidway\b", re.IGNORECASE),
            re.compile(r"\bcloudengine\b", re.IGNORECASE),
        ),
    ),
    (
        "samsung",
        (
            re.compile(r"\bsamsung\b", re.IGNORECASE),
            re.compile(r"\bubigate\b", re.IGNORECASE),
        ),
    ),
    (
        "juniper",
        (
            re.compile(r"\bjuniper\b", re.IGNORECASE),
            re.compile(r"\bjunos\b", re.IGNORECASE),
        ),
    ),
)


class ConsolePromptState(Enum):
    """Estado actual detectado en la consola serial del switch."""

    NO_RESPONSE = "NO_RESPONSE"
    PRESS_RETURN = "PRESS_RETURN"
    INITIAL_CONFIG_DIALOG = "INITIAL_CONFIG_DIALOG"
    LOGIN_USERNAME = "LOGIN_USERNAME"
    LOGIN_PASSWORD = "LOGIN_PASSWORD"
    USER_EXEC = "USER_EXEC"
    PRIV_EXEC = "PRIV_EXEC"
    UNKNOWN_PROMPT = "UNKNOWN_PROMPT"


@dataclass
class SerialPortInfo:
    """Metadatos de un puerto serial físico detectado en el host."""

    device: str
    description: str = ""
    manufacturer: str = ""
    vid: Optional[int] = None
    pid: Optional[int] = None
    kind: str = "USB_RJ45"
    priority: int = 10

    @property
    def short_label(self) -> str:
        """Etiqueta compacta para mostrar en la pantalla OLED."""
        base = os.path.basename(self.device)
        kind_tag = "USB-C/B" if self.kind == "USB_DIRECT" else "RJ45"
        return f"{base} ({kind_tag})"


@dataclass
class SerialProbeResult:
    """Resultado completo de la Fase 1 (detección pasiva de puerto, baudrate y prompt)."""

    port: Optional[SerialPortInfo] = None
    baudrate: int = 9600
    prompt_state: ConsolePromptState = ConsolePromptState.NO_RESPONSE
    banner_text: str = ""
    prompt_line: str = ""
    detected_vendor: Optional[str] = None
    hostname_hint: str = ""
    signals: Dict[str, bool] = field(default_factory=dict)
    error: Optional[str] = None
    handled_initial_dialog: bool = False

    @property
    def is_responsive(self) -> bool:
        """Indica si el switch devolvió texto ASCII legible en consola."""
        return self.prompt_state != ConsolePromptState.NO_RESPONSE and self.port is not None

    @property
    def requires_auth(self) -> bool:
        """Indica si el switch solicita usuario y/o contraseña antes de operar."""
        return self.prompt_state in (
            ConsolePromptState.LOGIN_USERNAME,
            ConsolePromptState.LOGIN_PASSWORD,
        )

    @property
    def is_privileged(self) -> bool:
        """Indica si la consola ya se encuentra en modo privilegiado (#)."""
        return self.prompt_state == ConsolePromptState.PRIV_EXEC


def clean_console_text(raw: bytes) -> str:
    """Decodifica bytes de consola serial eliminando secuencias ANSI y nulos."""
    if not raw:
        return ""
    text = raw.decode("latin-1", errors="ignore")
    text = text.replace("\x00", "")
    text = _ANSI_ESCAPE_RE.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text


def is_legible_ascii(raw: bytes, min_ratio: float = 0.85) -> bool:
    """
    Determina si un bloque de bytes corresponde a texto ASCII legible y no a
    ruido binario producto de un baudrate incorrecto.
    """
    if not raw:
        return False

    cleaned = raw.replace(b"\x00", b"")
    if not cleaned:
        return False

    # Quitar secuencias de escape ANSI antes de evaluar proporción imprimible
    text_no_ansi = _ANSI_ESCAPE_RE.sub("", cleaned.decode("latin-1", errors="ignore"))
    if not text_no_ansi.strip():
        return False

    total = len(text_no_ansi)
    printable = sum(
        1
        for ch in text_no_ansi
        if 32 <= ord(ch) <= 126 or ch in ("\r", "\n", "\t", "\x08")
    )
    ratio = printable / float(total)
    if ratio < min_ratio:
        return False

    # Debe contener al menos un carácter alfanumérico o símbolo típico de prompt
    has_meaningful = any(ch.isalnum() or ch in (">", "#", ":", "[", "]") for ch in text_no_ansi)
    return has_meaningful


def classify_prompt_state(text: str) -> Tuple[ConsolePromptState, str]:
    """
    Clasifica el estado actual del prompt a partir del texto capturado en consola.
    Devuelve (ConsolePromptState, ultima_linea_significativa).
    """
    if not text or not text.strip():
        return ConsolePromptState.NO_RESPONSE, ""

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return ConsolePromptState.NO_RESPONSE, ""

    last_line = lines[-1]
    tail_block = "\n".join(lines[-2:])

    # 1. Priorizar el estado exacto de la última línea activa (login o prompt CLI)
    if _LOGIN_USER_RE.search(last_line):
        return ConsolePromptState.LOGIN_USERNAME, last_line

    if _LOGIN_PASS_RE.search(last_line):
        return ConsolePromptState.LOGIN_PASSWORD, last_line

    if _PRIV_PROMPT_RE.match(last_line) or _HUAWEI_SYS_PROMPT_RE.match(last_line):
        return ConsolePromptState.PRIV_EXEC, last_line

    if _USER_PROMPT_RE.match(last_line):
        return ConsolePromptState.USER_EXEC, last_line

    # 2. Diálogos de arranque o bienvenida cuando aún no hay prompt CLI
    # Evaluar primero la última línea para que un mensaje antiguo no tape uno nuevo
    if _PRESS_RETURN_RE.search(last_line):
        return ConsolePromptState.PRESS_RETURN, last_line

    if _INITIAL_DIALOG_RE.search(last_line) or _INITIAL_DIALOG_RE.search(tail_block):
        return ConsolePromptState.INITIAL_CONFIG_DIALOG, last_line

    if _PRESS_RETURN_RE.search(tail_block):
        return ConsolePromptState.PRESS_RETURN, last_line

    return ConsolePromptState.UNKNOWN_PROMPT, last_line


def detect_vendor_passive(banner: str, prompt_line: str = "") -> Optional[str]:
    """
    Identifica de forma estrictamente pasiva el fabricante probable a partir del
    banner y la línea de prompt, sin enviar comandos al switch.
    """
    combined = f"{banner or ''}\n{prompt_line or ''}".strip()
    if not combined:
        return None

    for vendor_id, patterns in _PASSIVE_VENDOR_RULES:
        for pattern in patterns:
            if pattern.search(combined):
                return vendor_id

    return None


def extract_hostname_hint(prompt_line: str) -> str:
    """Extrae un nombre de host probable desde una línea de prompt CLI."""
    line = (prompt_line or "").strip()
    if not line:
        return ""

    # Formato MikroTik: [admin@Core-MT] >
    mt_match = re.match(r"^\[[^@\]]+@([^\]]+)\]\s*>", line)
    if mt_match:
        return mt_match.group(1).strip()

    # Formato Huawei: <Core-SW> o [Core-SW]
    hw_match = re.match(r"^[<\[]([A-Za-z0-9._\-]{1,48})[>\]]$", line)
    if hw_match:
        return hw_match.group(1).strip()

    # Formato estándar Cisco / TP-Link / Aruba / Samsung: Switch-2960S# o Switch>
    std_match = re.match(r"^([A-Za-z0-9._\-]{1,48})(?:\([^)]*\))?[#>]$", line)
    if std_match:
        return std_match.group(1).strip()

    return ""


class SerialConsoleManager:
    """
    Gestor de puertos seriales y auto-detección pasiva para switches gestionables.
    """

    def __init__(
        self,
        baudrates: Sequence[int] = DEFAULT_BAUDRATES,
        read_timeout_s: float = 1.1,
        serial_factory: Optional[Callable[..., Any]] = None,
    ):
        self.baudrates: Tuple[int, ...] = tuple(baudrates) if baudrates else DEFAULT_BAUDRATES
        self.read_timeout_s = max(0.15, float(read_timeout_s))
        self._serial_factory = serial_factory

    @staticmethod
    def is_mock_mode() -> bool:
        """Retorna True si la ejecución está en modo Dry-Run o Mock Serial."""
        return (
            os.getenv("REI_DRY_RUN") == "1"
            or os.getenv("REI_MOCK_SERIAL") == "1"
        )

    @staticmethod
    def _classify_port(device: str, description: str, manufacturer: str) -> Tuple[str, int]:
        """Clasifica el tipo de cable consola y asigna prioridad de escaneo."""
        dev_lower = device.lower()
        meta = f"{description} {manufacturer}".lower()
        is_direct = "ttyacm" in dev_lower
        kind = "USB_DIRECT" if is_direct else "USB_RJ45"

        priority = 20 if is_direct else 10
        for idx, kw in enumerate(_PREFERRED_CHIP_KEYWORDS):
            if kw in meta:
                priority = idx
                break
        return kind, priority

    def list_console_ports(self) -> List[SerialPortInfo]:
        """
        Descubre puertos seriales USB disponibles (/dev/ttyUSB* y /dev/ttyACM*).
        En modo simulación devuelve un puerto virtual consistente con el escenario activo.
        """
        if self.is_mock_mode():
            scenario = (os.getenv("REI_MOCK_SERIAL_SCENARIO") or "cisco_priv").strip().lower()
            if scenario == "no_port":
                return []
            if scenario == "cisco_minib":
                return [
                    SerialPortInfo(
                        device="/dev/ttyACM0",
                        description="Cisco USB Console (Simulado)",
                        manufacturer="Cisco Systems",
                        vid=0x05A6,
                        pid=0x0009,
                        kind="USB_DIRECT",
                        priority=1,
                    )
                ]
            return [
                SerialPortInfo(
                    device="/dev/ttyUSB0",
                    description="FT232R USB UART (Simulado)",
                    manufacturer="FTDI",
                    vid=0x0403,
                    pid=0x6001,
                    kind="USB_RJ45",
                    priority=0,
                )
            ]

        ports: List[SerialPortInfo] = []
        seen_devices = set()

        if PYSERIAL_AVAILABLE and list_ports is not None:
            try:
                for p in list_ports.comports():
                    dev = getattr(p, "device", "") or ""
                    if not (dev.startswith("/dev/ttyUSB") or dev.startswith("/dev/ttyACM")):
                        continue
                    desc = str(getattr(p, "description", "") or "")
                    mfg = str(getattr(p, "manufacturer", "") or "")
                    kind, priority = self._classify_port(dev, desc, mfg)
                    ports.append(
                        SerialPortInfo(
                            device=dev,
                            description=desc,
                            manufacturer=mfg,
                            vid=getattr(p, "vid", None),
                            pid=getattr(p, "pid", None),
                            kind=kind,
                            priority=priority,
                        )
                    )
                    seen_devices.add(dev)
            except OSError as ex:
                logger.warning(f"Error enumerando puertos con pyserial: {ex}")

        # Respaldo directo en /dev por si list_ports no está disponible o falla udev
        if os.path.isdir("/dev"):
            try:
                for entry in sorted(os.listdir("/dev")):
                    if not (entry.startswith("ttyUSB") or entry.startswith("ttyACM")):
                        continue
                    dev = f"/dev/{entry}"
                    if dev in seen_devices:
                        continue
                    kind, priority = self._classify_port(dev, "", "")
                    ports.append(
                        SerialPortInfo(
                            device=dev,
                            description="Consola USB",
                            manufacturer="",
                            kind=kind,
                            priority=priority,
                        )
                    )
                    seen_devices.add(dev)
            except OSError as ex:
                logger.debug(f"No se pudo inspeccionar /dev para puertos seriales: {ex}")

        ports.sort(key=lambda item: (item.priority, item.device))
        return ports

    def _open_serial(self, device: str, baudrate: int) -> Any:
        """Abre un puerto serial físico o mediante fábrica inyectada en pruebas."""
        if self._serial_factory is not None:
            ser = self._serial_factory(
                port=device,
                baudrate=baudrate,
                bytesize=8,
                parity="N",
                stopbits=1,
                timeout=self.read_timeout_s,
                xonxoff=False,
                rtscts=False,
                dsrdtr=False,
            )
            return ser

        if not PYSERIAL_AVAILABLE or serial is None:
            raise RuntimeError("pyserial no está instalado en el entorno Python.")

        ser = serial.Serial(
            port=device,
            baudrate=baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=self.read_timeout_s,
            write_timeout=1.0,
            xonxoff=False,
            rtscts=False,
            dsrdtr=False,
        )
        # Activar DTR/RTS explícitamente: requerido por consolas USB CDC-ACM (Cisco Mini-B)
        # para activar el puente UART interno y el cambio de medio RJ45 <-> USB.
        try:
            ser.dtr = True
            ser.rts = True
        except (OSError, ValueError, AttributeError):
            pass
        time.sleep(0.30)
        return ser

    @staticmethod
    def _read_signals(ser: Any) -> Dict[str, bool]:
        """Lee señales de control RS-232 (DSR, CTS, CD) de forma segura."""
        signals: Dict[str, bool] = {}
        for attr in ("dsr", "cts", "cd"):
            try:
                val = getattr(ser, attr, None)
                if val is not None:
                    signals[attr] = bool(val)
            except (OSError, ValueError):
                pass
        return signals

    def _read_available(
        self,
        ser: Any,
        wait_s: float,
        idle_gap_s: Optional[float] = None,
        max_total_s: float = 2.6,
    ) -> bytes:
        """
        Lee todos los datos disponibles del buffer serial drenando banners largos
        (ej. MOTD ASCII a 9600 bps) hasta observar un periodo real de silencio.
        """
        if idle_gap_s is None:
            idle_gap_s = 0.04 if self._serial_factory is not None else 0.35

        start_ts = time.monotonic()
        first_byte_deadline = start_ts + max(0.15, wait_s)
        hard_deadline = start_ts + max(wait_s + 0.5, max_total_s)
        last_data_ts: Optional[float] = None
        chunks: List[bytes] = []

        while True:
            now = time.monotonic()
            if now >= hard_deadline:
                break
            if last_data_ts is None and now >= first_byte_deadline:
                break
            if last_data_ts is not None and (now - last_data_ts) >= idle_gap_s:
                break

            try:
                waiting = int(getattr(ser, "in_waiting", 0) or 0)
            except (OSError, ValueError):
                waiting = 0

            try:
                if waiting > 0:
                    data = ser.read(waiting)
                elif last_data_ts is None:
                    data = ser.read(256)
                else:
                    time.sleep(0.03)
                    continue
            except (OSError, SerialException):
                break

            if data:
                chunks.append(data)
                last_data_ts = time.monotonic()
            else:
                time.sleep(0.03)

        return b"".join(chunks)

    def _mock_probe_result(
        self,
        port: SerialPortInfo,
        baudrates: Sequence[int],
        progress_cb: Optional[Callable[[str, float], None]] = None,
    ) -> SerialProbeResult:
        """Construye un resultado de sondeo realista para modo simulación."""
        scenario = (os.getenv("REI_MOCK_SERIAL_SCENARIO") or "cisco_priv").strip().lower()
        if progress_cb:
            progress_cb(f"Probando {baudrates[0] if baudrates else 9600} bps...", 0.20)
        time.sleep(0.15)

        if scenario == "no_response":
            return SerialProbeResult(
                port=port,
                baudrate=baudrates[0] if baudrates else 9600,
                prompt_state=ConsolePromptState.NO_RESPONSE,
                signals={"dsr": False, "cts": False},
                error="Sin respuesta del switch en consola serial.",
            )

        if scenario == "cisco_login":
            banner = "User Access Verification\n\nUsername:"
            return SerialProbeResult(
                port=port,
                baudrate=9600,
                prompt_state=ConsolePromptState.LOGIN_USERNAME,
                banner_text=banner,
                prompt_line="Username:",
                detected_vendor="cisco_ios",
                hostname_hint="",
                signals={"dsr": True, "cts": True},
            )

        if scenario == "cisco_password_only":
            banner = "User Access Verification\n\nPassword:"
            return SerialProbeResult(
                port=port,
                baudrate=9600,
                prompt_state=ConsolePromptState.LOGIN_PASSWORD,
                banner_text=banner,
                prompt_line="Password:",
                detected_vendor="cisco_ios",
                hostname_hint="",
                signals={"dsr": True, "cts": True},
            )

        if scenario == "cisco_user":
            banner = "\nCisco IOS Software, C2960S Software (C2960S-UNIVERSALK9-M)\nSW-2960S-POE>"
            return SerialProbeResult(
                port=port,
                baudrate=9600,
                prompt_state=ConsolePromptState.USER_EXEC,
                banner_text=banner,
                prompt_line="SW-2960S-POE>",
                detected_vendor="cisco_ios",
                hostname_hint="SW-2960S-POE",
                signals={"dsr": True, "cts": True},
            )

        if scenario == "tplink_login":
            banner = "TP-LINK JetStream L2 Managed Switch\n\nUser:"
            return SerialProbeResult(
                port=port,
                baudrate=38400,
                prompt_state=ConsolePromptState.LOGIN_USERNAME,
                banner_text=banner,
                prompt_line="User:",
                detected_vendor="tplink_jetstream",
                hostname_hint="",
                signals={"dsr": True, "cts": True},
            )

        # Escenario por defecto: Catalyst 2960-S PoE 10G en modo privilegiado
        banner = (
            "Cisco Catalyst 2960-S Series PoE 10G\n"
            "con0 is now available\n\n"
            "SW-2960S-POE#"
        )
        return SerialProbeResult(
            port=port,
            baudrate=9600,
            prompt_state=ConsolePromptState.PRIV_EXEC,
            banner_text=banner,
            prompt_line="SW-2960S-POE#",
            detected_vendor="cisco_ios",
            hostname_hint="SW-2960S-POE",
            signals={"dsr": True, "cts": True},
        )

    def probe_port(
        self,
        port: SerialPortInfo,
        baudrates: Optional[Sequence[int]] = None,
        progress_cb: Optional[Callable[[str, float], None]] = None,
    ) -> SerialProbeResult:
        """
        Ejecuta Fase 1 sobre un puerto específico: prueba cada baudrate en orden
        enviando únicamente '\\r\\n' (sin comandos ni contraseñas) hasta recibir
        una respuesta ASCII legible.
        """
        rates = tuple(baudrates) if baudrates else self.baudrates
        if not rates:
            rates = DEFAULT_BAUDRATES

        if self.is_mock_mode() and self._serial_factory is None:
            return self._mock_probe_result(port, rates, progress_cb=progress_cb)

        last_error: Optional[str] = None
        last_signals: Dict[str, bool] = {}

        for idx, baud in enumerate(rates):
            if progress_cb:
                pct = 0.12 + (0.18 * idx / max(1, len(rates)))
                progress_cb(f"Probando {baud} bps...", pct)

            ser = None
            try:
                ser = self._open_serial(port.device, baud)
                last_signals = self._read_signals(ser)

                try:
                    if hasattr(ser, "reset_input_buffer"):
                        ser.reset_input_buffer()
                    if hasattr(ser, "reset_output_buffer"):
                        ser.reset_output_buffer()
                except (OSError, SerialException):
                    pass

                # Enviar CR simple (\r) para despertar consola de forma pasiva (estándar Cisco/IOS).
                # Si la consola está muda (ej. congelada en 'Translating "..."' por DNS lookup),
                # en el 3er intento enviamos Ctrl+Shift+6 (\x1e) + \r para liberarla sin mandar comandos.
                raw_acc = b""
                saw_garbled = False
                for attempt in range(3):
                    if attempt == 2:
                        if saw_garbled:
                            # Bytes corruptos confirman baudrate incorrecto: cambiar de velocidad ya
                            break
                        ser.write(b"\x1e\r")
                    else:
                        ser.write(b"\r")
                    if hasattr(ser, "flush"):
                        ser.flush()
                    chunk = self._read_available(ser, wait_s=self.read_timeout_s)
                    raw_acc += chunk
                    if is_legible_ascii(raw_acc):
                        break
                    if raw_acc.strip(b"\x00\r\n "):
                        saw_garbled = True
                        if attempt >= 1:
                            break
                    if self._serial_factory is None:
                        time.sleep(0.25)

                if not is_legible_ascii(raw_acc):
                    # En consolas USB nativas de Cisco (05a6:0009), si 9600 no devolvió
                    # ni siquiera ruido binario, el switch probablemente aún está arrancando;
                    # evitar inyectar tramas a 115200/38400 que disparen DNS lookup en IOS.
                    if port.vid == 0x05A6 and baud == 9600 and not saw_garbled:
                        last_error = "Sin respuesta en consola Cisco (9600 bps). Verifique si el switch termino de iniciar."
                        break
                    continue

                text = clean_console_text(raw_acc)
                state, prompt_line = classify_prompt_state(text)
                handled_dialog = False

                # Si la consola estaba trabada en DNS lookup ('Translating "..."'), abortar con Ctrl+Shift+6
                if state == ConsolePromptState.UNKNOWN_PROMPT and _TRANSLATING_RE.search(text):
                    ser.write(b"\x1e\r")
                    if hasattr(ser, "flush"):
                        ser.flush()
                    extra_abort = self._read_available(ser, wait_s=self.read_timeout_s + 0.5)
                    if is_legible_ascii(extra_abort):
                        text = f"{text}\n{clean_console_text(extra_abort)}".strip()
                        state, prompt_line = classify_prompt_state(text)

                # Si recibimos texto sin prompt definido (ej. banner largo o fin de arranque),
                # enviar un CR adicional para revelar el prompt real
                if state == ConsolePromptState.UNKNOWN_PROMPT:
                    ser.write(b"\r")
                    if hasattr(ser, "flush"):
                        ser.flush()
                    extra_prompt = self._read_available(ser, wait_s=self.read_timeout_s + 0.4)
                    if is_legible_ascii(extra_prompt):
                        text = f"{text}\n{clean_console_text(extra_prompt)}".strip()
                        state, prompt_line = classify_prompt_state(text)

                # Si pide "Press RETURN to get started", dar un respiro y enviar CR simple
                if state == ConsolePromptState.PRESS_RETURN:
                    if self._serial_factory is None:
                        time.sleep(0.35)
                    ser.write(b"\r")
                    if hasattr(ser, "flush"):
                        ser.flush()
                    extra = self._read_available(
                        ser,
                        wait_s=self.read_timeout_s + 0.6,
                        max_total_s=3.2,
                    )
                    if is_legible_ascii(extra):
                        text = f"{text}\n{clean_console_text(extra)}".strip()
                        state, prompt_line = classify_prompt_state(text)

                # Si aparece el asistente inicial de fábrica ([yes/no]), responder 'no'
                if state == ConsolePromptState.INITIAL_CONFIG_DIALOG:
                    ser.write(b"no\r")
                    if hasattr(ser, "flush"):
                        ser.flush()
                    handled_dialog = True
                    extra = self._read_available(ser, wait_s=self.read_timeout_s + 0.8)
                    if is_legible_ascii(extra):
                        text = f"{text}\n{clean_console_text(extra)}".strip()
                        state, prompt_line = classify_prompt_state(text)
                        if state == ConsolePromptState.PRESS_RETURN:
                            ser.write(b"\r")
                            extra2 = self._read_available(ser, wait_s=self.read_timeout_s)
                            if is_legible_ascii(extra2):
                                text = f"{text}\n{clean_console_text(extra2)}".strip()
                                state, prompt_line = classify_prompt_state(text)

                vendor = detect_vendor_passive(text, prompt_line)
                if vendor is None:
                    # Pista pasiva desde el descriptor USB (ej. "Cisco USB Console" en Mini-B)
                    usb_meta = f"{port.manufacturer} {port.description}".strip()
                    if usb_meta:
                        vendor = detect_vendor_passive(usb_meta, "")
                hostname_hint = extract_hostname_hint(prompt_line)

                logger.info(
                    f"Consola detectada en {port.device} @ {baud} bps "
                    f"(estado={state.value}, marca={vendor or 'desconocida'}, host={hostname_hint or 'N/D'})"
                )

                return SerialProbeResult(
                    port=port,
                    baudrate=baud,
                    prompt_state=state,
                    banner_text=text.strip(),
                    prompt_line=prompt_line,
                    detected_vendor=vendor,
                    hostname_hint=hostname_hint,
                    signals=last_signals,
                    handled_initial_dialog=handled_dialog,
                )

            except PermissionError:
                last_error = f"Sin permisos en {port.device}"
                logger.error(f"Permiso denegado al abrir {port.device}")
                break
            except (OSError, SerialException, RuntimeError) as ex:
                last_error = str(ex)
                logger.warning(f"Error probando {port.device} a {baud} bps: {ex}")
            finally:
                if ser is not None:
                    try:
                        ser.close()
                    except (OSError, SerialException):
                        pass

        return SerialProbeResult(
            port=port,
            baudrate=rates[0],
            prompt_state=ConsolePromptState.NO_RESPONSE,
            signals=last_signals,
            error=last_error or "Sin respuesta en consola serial.",
        )

    def auto_probe(
        self,
        baudrates: Optional[Sequence[int]] = None,
        progress_cb: Optional[Callable[[str, float], None]] = None,
    ) -> SerialProbeResult:
        """
        Descubre puertos seriales disponibles y ejecuta detección pasiva sobre
        cada uno en orden de prioridad hasta encontrar un switch que responda.
        """
        if progress_cb:
            progress_cb("Buscando cable serial...", 0.08)

        ports = self.list_console_ports()
        if not ports:
            return SerialProbeResult(
                port=None,
                prompt_state=ConsolePromptState.NO_RESPONSE,
                error="No se detecto cable consola USB (/dev/ttyUSB* o /dev/ttyACM*).",
            )

        last_result: Optional[SerialProbeResult] = None
        for port in ports:
            res = self.probe_port(port, baudrates=baudrates, progress_cb=progress_cb)
            if res.is_responsive:
                return res
            last_result = res

        return last_result or SerialProbeResult(
            port=ports[0],
            prompt_state=ConsolePromptState.NO_RESPONSE,
            error="El switch no respondio en los baudrates probados.",
        )
