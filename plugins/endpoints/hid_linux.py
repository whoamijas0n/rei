"""
REI - Linux USB HID Diagnostic Plugin (plugins/endpoints/hid_linux.py)
Generates non-destructive Bash payloads and orchestrates Rubber Ducky injection
for Linux endpoints with automated JSON exfiltration to REI's local FastAPI server.
"""

import base64
import json
import logging
import time
from typing import Any, Callable, Dict, List, Optional

from core.ducky import DuckyInjector
from core.interfaces import (
    DiagnosticMetric,
    DiagnosticResult,
    DiagnosticStatus,
    IDiagnosticPlugin,
    Severity,
)

logger = logging.getLogger("REI.Plugins.Endpoints.Linux")


class LinuxPayloadGenerator:
    """
    Constructs compact, non-destructive Bash scripts and lightweight micro-stager commands
    for target Linux endpoints.
    """

    @classmethod
    def normalize_category(cls, category: str) -> str:
        """Normalizes diagnostic category names to standard canonical tokens."""
        cat = category.upper().strip()
        if "RED" in cat or "CONEXION" in cat or "NETWORK" in cat:
            return "RED"
        elif "HARDWARE" in cat or "CPU" in cat:
            return "HARDWARE"
        elif "MALWARE" in cat or "VIRUS" in cat or "PROCESOS" in cat:
            return "MALWARE"
        elif "OTROS" in cat or "LOGS" in cat or "REGISTROS" in cat:
            return "LOGS"
        return "COMPLETO"

    @classmethod
    def get_bash_script(cls, category: str, server_url: str = "http://10.0.0.1:8000") -> str:
        """
        Returns the raw Bash script that gathers telemetry and posts JSON via curl or wget.
        Escapes and cleans outputs to ensure 100% valid JSON payload.
        """
        cat = cls.normalize_category(category)
        endpoint_uri = f"{server_url.rstrip('/')}/api/v1/endpoint/report"

        if cat == "RED":
            telemetry_sh = (
                "def_rt=$(ip -4 route show default 2>/dev/null | grep -v '10.0.0.' | head -n 1);"
                "if [ -z \"$def_rt\" ]; then def_rt=$(ip -4 route show default 2>/dev/null | head -n 1); fi;"
                "gw=$(echo \"$def_rt\" | awk '{for(i=1;i<=NF;i++) if($i==\"via\") print $(i+1); else if($i==\"default\" && $(i+1)!=\"dev\") print $3}' | head -n 1 | tr -d '\"\\\\\\r\\n');"
                "iface=$(echo \"$def_rt\" | awk '{for(i=1;i<=NF;i++) if($i==\"dev\") print $(i+1)}' | head -n 1 | tr -d '\"\\\\\\r\\n');"
                "ip=$(ip -4 addr show dev \"$iface\" 2>/dev/null | grep inet | awk '{print $2}' | head -n 1 | tr -d '\"\\\\\\r\\n');"
                "if [ -z \"$ip\" ]; then ip=$(ip -4 addr show up 2>/dev/null | grep -v '127.0.0.1' | grep inet | awk '{print $2}' | head -n 1 | tr -d '\"\\\\\\r\\n'); fi;"
                "mac=$(cat /sys/class/net/$iface/address 2>/dev/null | tr -d '\"\\\\\\r\\n');"
                "speed=$(cat /sys/class/net/$iface/speed 2>/dev/null | tr -d '\"\\\\\\r\\n' || echo 'N/A');"
                "operstate=$(cat /sys/class/net/$iface/operstate 2>/dev/null | tr -d '\"\\\\\\r\\n' || echo 'up');"
                "carrier=$(cat /sys/class/net/$iface/carrier 2>/dev/null | tr -d '\"\\\\\\r\\n' || echo '1');"
                "mtu=$(cat /sys/class/net/$iface/mtu 2>/dev/null | tr -d '\"\\\\\\r\\n' || echo '1500');"
                "dns=$(grep nameserver /etc/resolv.conf 2>/dev/null | awk '{print $2}' | tr '\\n' ',' | sed 's/,$//' | tr -d '\"\\\\\\r');"
                "dns_ok=$(getent ahosts google.com >/dev/null 2>&1 && echo true || echo false);"
                "http_code=$(curl -s -o /dev/null -w \"%{http_code}\" --max-time 2 http://connectivitycheck.gstatic.com/generate_204 2>/dev/null || echo 0);"
                "captive=$( [ \"$http_code\" != \"204\" ] && [ \"$http_code\" != \"200\" ] && [ \"$http_code\" != \"0\" ] && echo true || echo false );"
                "tcp_conns=$(ss -t state established 2>/dev/null | wc -l | tr -d ' ' || echo 0);"
                "ping_gw=$( [ -n \"$gw\" ] && ping -c 2 -W 1 \"$gw\" >/dev/null 2>&1 && echo true || echo false );"
                "ping_ext=$(ping -c 2 -W 1 8.8.8.8 >/dev/null 2>&1 && echo true || echo false );"
                "gw_rtt=$( [ -n \"$gw\" ] && ping -c 2 -W 1 \"$gw\" 2>/dev/null | awk -F'/' '/rtt/ {print $5}' | tr -d '\"\\\\\\r\\n' || echo '0' );"
                "hops=$(traceroute -n -q 1 -w 1 -m 4 8.8.8.8 2>/dev/null | awk 'NR>1 {print $1\":\"$2}' | tr '\\n' ';' | sed 's/;$//' | tr -d '\"\\\\\\r');"
                "arp_gw=$( [ -n \"$gw\" ] && ip neigh show \"$gw\" 2>/dev/null | awk '{print $5}' | head -n 1 | tr -d '\"\\\\\\r\\n' || echo '' );"
                "wifi_ssid=$(iwgetid -r 2>/dev/null || nmcli -t -f active,ssid dev wifi 2>/dev/null | grep -E '^(sí|yes):' | cut -d: -f2 | head -n 1 | tr -d '\"\\\\\\r\\n');"
                "wifi_sig=$(nmcli -t -f in-use,signal dev wifi 2>/dev/null | grep '^\\*' | cut -d: -f2 | head -n 1 | tr -d '\"\\\\\\r\\n' || echo '');"
                "osi=\"{\\\"l7_application\\\":{\\\"dns_servers\\\":\\\"$dns\\\",\\\"dns_ok\\\":$dns_ok,\\\"http_status\\\":$http_code,\\\"captive_portal\\\":$captive},\\\"l6_l5_session\\\":{\\\"active_tcp_conns\\\":${tcp_conns:-0}},\\\"l3_network\\\":{\\\"ip\\\":\\\"$ip\\\",\\\"gateway\\\":\\\"$gw\\\",\\\"ping_gateway\\\":$ping_gw,\\\"ping_internet\\\":$ping_ext,\\\"gateway_rtt_ms\\\":\\\"${gw_rtt:-0}\\\",\\\"mtu\\\":\\\"$mtu\\\",\\\"traceroute_hops\\\":\\\"$hops\\\"},\\\"l2_datalink\\\":{\\\"interface\\\":\\\"$iface\\\",\\\"mac\\\":\\\"$mac\\\",\\\"speed\\\":\\\"$speed\\\",\\\"gateway_arp\\\":\\\"$arp_gw\\\",\\\"wifi_ssid\\\":\\\"$wifi_ssid\\\",\\\"wifi_signal\\\":\\\"$wifi_sig\\\"},\\\"l1_physical\\\":{\\\"carrier\\\":\\\"$carrier\\\",\\\"operstate\\\":\\\"$operstate\\\"}}\";"
                "t=\"{\\\"ip\\\":\\\"$ip\\\",\\\"gateway\\\":\\\"$gw\\\",\\\"mac\\\":\\\"$mac\\\",\\\"dns\\\":\\\"$dns\\\",\\\"ping_gateway\\\":$ping_gw,\\\"ping_internet\\\":$ping_ext,\\\"hardware\\\":$hw,\\\"osi_network\\\":$osi}\";"
            )
        elif cat == "HARDWARE":
            telemetry_sh = (
                "cpu_cores=$(nproc 2>/dev/null || grep -c ^processor /proc/cpuinfo 2>/dev/null || echo 1);"
                "cpu_cur_mhz=$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq 2>/dev/null | awk '{printf(\"%.0f\", $1/1000)}' || lscpu 2>/dev/null | grep 'CPU MHz' | awk '{print $NF}' | cut -d'.' -f1 || echo 0);"
                "cpu_max_mhz=$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_max_freq 2>/dev/null | awk '{printf(\"%.0f\", $1/1000)}' || lscpu 2>/dev/null | grep 'CPU max MHz' | awk '{print $NF}' | cut -d'.' -f1 || echo 0);"
                "cpu_load=$(top -bn1 2>/dev/null | grep 'Cpu(s)' | awk '{print $2 + $4}' | cut -d'.' -f1 | tr -d '\"\\\\\\r\\n');"
                "temp=$(cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null | awk '{printf(\"%.1f\", $1/1000)}' | tr -d '\"\\\\\\r\\n' || echo 0);"
                "ram_tot=$(free -m 2>/dev/null | awk '/Mem:/ {printf(\"%.1f\", $2/1024)}' || echo 0);"
                "ram_free=$(free -m 2>/dev/null | awk '/Mem:/ {printf(\"%.1f\", $7/1024)}' || echo 0);"
                "ram_used=$(free -m 2>/dev/null | awk '/Mem:/ {printf(\"%.1f\", ($2-$7)/1024)}' || echo 0);"
                "ram_pct=$(free 2>/dev/null | grep Mem | awk '{printf(\"%.1f\", ($3/$2)*100.0)}' | tr -d '\"\\\\\\r\\n' || echo 0);"
                "board_mfr=$(cat /sys/class/dmi/id/board_vendor 2>/dev/null | tr -d '\"\\\\\\r\\n' || echo 'N/A');"
                "board_prod=$(cat /sys/class/dmi/id/board_name 2>/dev/null | tr -d '\"\\\\\\r\\n' || echo 'N/A');"
                "bios_ver=$(cat /sys/class/dmi/id/bios_version 2>/dev/null | tr -d '\"\\\\\\r\\n' || echo 'N/A');"
                "bios_date=$(cat /sys/class/dmi/id/bios_date 2>/dev/null | tr -d '\"\\\\\\r\\n' || echo 'N/A');"
                "bat_cap=$(cat /sys/class/power_supply/BAT0/capacity 2>/dev/null || cat /sys/class/power_supply/BAT1/capacity 2>/dev/null || echo '');"
                "bat_stat=$(cat /sys/class/power_supply/BAT0/status 2>/dev/null || cat /sys/class/power_supply/BAT1/status 2>/dev/null || echo '');"
                "p_disks=$(lsblk -d -n -o NAME,MODEL,SIZE,ROTA,TRAN 2>/dev/null | awk '{printf(\"{\\\"name\\\":\\\"%s\\\",\\\"model\\\":\\\"%s\\\",\\\"size\\\":\\\"%s\\\",\\\"rotational\\\":%s,\\\"bus\\\":\\\"%s\\\"},\", $1, $2, $3, ($4==\"1\"?\"true\":\"false\"), $5)}' | sed 's/,$//');"
                "vols=$(df -h -x tmpfs -x devtmpfs -x squashfs 2>/dev/null | awk 'NR>1 {printf(\"{\\\"drive\\\":\\\"%s\\\",\\\"size\\\":\\\"%s\\\",\\\"used\\\":\\\"%s\\\",\\\"free\\\":\\\"%s\\\",\\\"use_pct\\\":\\\"%s\\\",\\\"mount\\\":\\\"%s\\\"},\", $1, $2, $3, $4, $5, $6)}' | sed 's/,$//');"
                "gpu_desc=$(lspci 2>/dev/null | grep -E 'VGA|3D|Display' | cut -d: -f3 | xargs | tr -d '\"\\\\\\r\\n' || echo 'N/A');"
                "hw_audit=\"{\\\"system\\\":{\\\"manufacturer\\\":\\\"$mfr\\\",\\\"model\\\":\\\"$model\\\",\\\"serial\\\":\\\"$serial\\\",\\\"board_mfr\\\":\\\"$board_mfr\\\",\\\"board_product\\\":\\\"$board_prod\\\",\\\"bios_version\\\":\\\"$bios_ver\\\",\\\"bios_date\\\":\\\"$bios_date\\\",\\\"os_name\\\":\\\"$os_name\\\",\\\"kernel\\\":\\\"$kernel\\\"},\\\"cpu\\\":{\\\"name\\\":\\\"$cpu_m\\\",\\\"cores\\\":${cpu_cores:-1},\\\"threads\\\":${cpu_cores:-1},\\\"max_mhz\\\":${cpu_max_mhz:-0},\\\"current_mhz\\\":${cpu_cur_mhz:-0},\\\"load_pct\\\":${cpu_load:-0},\\\"temp_c\\\":${temp:-0}},\\\"memory\\\":{\\\"total_gb\\\":${ram_tot:-0},\\\"free_gb\\\":${ram_free:-0},\\\"used_gb\\\":${ram_used:-0},\\\"usage_pct\\\":${ram_pct:-0}},\\\"storage\\\":{\\\"physical_drives\\\":[${p_disks}],\\\"volumes\\\":[${vols}]},\\\"graphics\\\":[{\\\"name\\\":\\\"$gpu_desc\\\"}],\\\"battery\\\":$( [ -n \"$bat_cap\" ] && echo \"{\\\"present\\\":true,\\\"charge_pct\\\":$bat_cap,\\\"status\\\":\\\"$bat_stat\\\"}\" || echo 'null' )}\";"
                "t=\"{\\\"cpu_percent\\\":${cpu_load:-0},\\\"ram_percent\\\":${ram_pct:-0},\\\"cpu_temp_c\\\":${temp:-0},\\\"hardware\\\":$hw,\\\"hardware_audit\\\":$hw_audit}\";"
            )
        elif cat == "MALWARE":
            telemetry_sh = (
                "av_name=$(command -v clamdscan >/dev/null && echo 'ClamAV' || (command -v rkhunter >/dev/null && echo 'rkhunter' || echo 'Ninguno'));"
                "av_act=$( [ \"$av_name\" != \"Ninguno\" ] && echo true || echo false );"
                "fw_on=$( (command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q 'active') || iptables -L -n 2>/dev/null | grep -q 'Chain' && echo true || echo false );"
                "sec_mod=\"Ninguno\";"
                "if [ -d /sys/kernel/security/apparmor ]; then sec_mod=\"AppArmor\"; elif command -v getenforce >/dev/null 2>&1; then sec_mod=\"SELinux-$(getenforce 2>/dev/null)\"; fi;"
                "defenses=\"{\\\"antivirus_name\\\":\\\"$av_name\\\",\\\"antivirus_active\\\":$av_act,\\\"firewall_enabled\\\":$fw_on,\\\"security_module\\\":\\\"$sec_mod\\\"}\";"
                "susp_p=\"\"; susp_p_cnt=0;"
                "for exe in /proc/[0-9]*/exe; do "
                "[ -r \"$exe\" ] 2>/dev/null || continue;"
                "tgt=$(readlink \"$exe\" 2>/dev/null);"
                "[ -z \"$tgt\" ] && continue;"
                "pid=$(basename $(dirname \"$exe\"));"
                "pname=$(cat /proc/$pid/comm 2>/dev/null | tr -d '\"\\\\\\r\\n' || echo 'proc');"
                "rsn=\"\";"
                "case \"$tgt\" in "
                "/tmp/*|/dev/shm/*|/var/tmp/*) rsn=\"Ejecucion desde directorio temporal\";;"
                "*\\(deleted\\)*) rsn=\"Binario eliminado en memoria (deleted)\";;"
                "esac;"
                "if [ -n \"$rsn\" ]; then "
                "susp_p=\"${susp_p}{\\\"pid\\\":$pid,\\\"name\\\":\\\"$pname\\\",\\\"path\\\":\\\"$tgt\\\",\\\"reason\\\":\\\"$rsn\\\"},\";"
                "susp_p_cnt=$((susp_p_cnt + 1));"
                "fi;"
                "done;"
                "susp_p=$(echo \"$susp_p\" | sed 's/,$//');"
                "persist=\"\";"
                "crons=$(crontab -l 2>/dev/null | grep -v '^#' | grep -E '[a-zA-Z0-9]' | head -n 5 | tr ' ' '_' | tr '\\n' ' ');"
                "for c in $crons; do "
                "c_clean=$(echo \"$c\" | tr '_' ' ' | tr -d '\"\\\\\\r\\n' | cut -c 1-50);"
                "persist=\"${persist}{\\\"type\\\":\\\"Crontab\\\",\\\"name\\\":\\\"cron\\\",\\\"path\\\":\\\"$c_clean\\\",\\\"location\\\":\\\"user_crontab\\\"},\";"
                "done;"
                "u_units=$(systemctl --user list-unit-files --state=enabled 2>/dev/null | grep -E '\\.(service|timer)' | awk '{print $1}' | head -n 5);"
                "for un in $u_units; do "
                "[ -z \"$un\" ] && continue;"
                "persist=\"${persist}{\\\"type\\\":\\\"Systemd User\\\",\\\"name\\\":\\\"$un\\\",\\\"path\\\":\\\"$un\\\",\\\"location\\\":\\\"~/.config/systemd\\\"},\";"
                "done;"
                "persist=$(echo \"$persist\" | sed 's/,$//');"
                "c2_p=\"4444|1337|6667|8888|9001|31337\";"
                "susp_conns=\"\"; c2_found=false;"
                "conns=$(ss -tunp state established 2>/dev/null | awk 'NR>1 {print $4\"#\"$5\"#\"$6}' | head -n 10);"
                "for c in $conns; do "
                "[ -z \"$c\" ] && continue;"
                "l_addr=$(echo \"$c\" | cut -d'#' -f1);"
                "r_addr=$(echo \"$c\" | cut -d'#' -f2);"
                "proc_f=$(echo \"$c\" | cut -d'#' -f3 | tr -d '\"\\\\\\r\\n');"
                "r_ip=$(echo \"$r_addr\" | cut -d: -f1);"
                "r_port=$(echo \"$r_addr\" | awk -F: '{print $NF}');"
                "l_port=$(echo \"$l_addr\" | awk -F: '{print $NF}');"
                "is_c2=false;"
                "if echo \"$r_port\" | grep -qE \"^($c2_p)$\"; then is_c2=true; c2_found=true; fi;"
                "susp_conns=\"${susp_conns}{\\\"remote_ip\\\":\\\"$r_ip\\\",\\\"remote_port\\\":${r_port:-0},\\\"local_port\\\":${l_port:-0},\\\"process\\\":\\\"$proc_f\\\",\\\"suspicious\\\":$is_c2},\";"
                "done;"
                "susp_conns=$(echo \"$susp_conns\" | sed 's/,$//');"
                "listen_p=$(ss -tulpn 2>/dev/null | grep LISTEN | awk '{print $5}' | awk -F: '{print $NF}' | sort -u | head -n 10 | awk '{printf(\"{\\\"port\\\":%s},\", $1)}' | sed 's/,$//');"
                "hosts_bad=false;"
                "if grep -qE '(?i)(google|microsoft|virustotal|kaspersky|symantec)' /etc/hosts 2>/dev/null; then hosts_bad=true; fi;"
                "temp_art=\"\"; temp_art_cnt=0;"
                "arts=$(find /tmp /dev/shm -maxdepth 2 -type f -executable -mtime -7 2>/dev/null | head -n 5);"
                "for a in $arts; do "
                "[ -z \"$a\" ] && continue;"
                "aname=$(basename \"$a\" | tr -d '\"\\\\\\r\\n');"
                "asize=$(ls -sk \"$a\" 2>/dev/null | awk '{print $1}');"
                "adate=$(date -r \"$a\" '+%Y-%m-%d' 2>/dev/null || echo 'reciente');"
                "temp_art=\"${temp_art}{\\\"name\\\":\\\"$aname\\\",\\\"path\\\":\\\"$a\\\",\\\"size_kb\\\":${asize:-0},\\\"date\\\":\\\"$adate\\\"},\";"
                "temp_art_cnt=$((temp_art_cnt + 1));"
                "done;"
                "temp_art=$(echo \"$temp_art\" | sed 's/,$//');"
                "t_score=0;"
                "if [ \"$av_act\" = \"false\" ]; then t_score=$((t_score + 25)); fi;"
                "if [ $susp_p_cnt -gt 0 ]; then t_score=$((t_score + 30 * susp_p_cnt)); fi;"
                "if [ \"$hosts_bad\" = \"true\" ]; then t_score=$((t_score + 25)); fi;"
                "if [ $temp_art_cnt -gt 0 ]; then t_score=$((t_score + 15)); fi;"
                "if [ \"$c2_found\" = \"true\" ]; then t_score=$((t_score + 35)); fi;"
                "t_lvl=\"BAJO\";"
                "if [ $t_score -ge 50 ]; then t_lvl=\"CRITICO\"; elif [ $t_score -ge 20 ]; then t_lvl=\"MEDIO\"; fi;"
                "malware_audit=\"{\\\"defenses\\\":$defenses,\\\"suspicious_processes\\\":[${susp_p}],\\\"persistence\\\":[${persist}],\\\"network_c2\\\":{\\\"established_connections\\\":[${susp_conns}],\\\"listening_ports\\\":[${listen_p}],\\\"hosts_file_hijack\\\":$hosts_bad},\\\"recent_artifacts\\\":[${temp_art}],\\\"threat_score\\\":$t_score,\\\"threat_level\\\":\\\"$t_lvl\\\"}\";"
                "top_p=$(ps -eo comm,%cpu --sort=-%cpu 2>/dev/null | head -n 6 | tail -n +2 | tr -d '\"\\\\\\r\\n' | tr '\\n' ',' | sed 's/,$//');"
                "t=\"{\\\"antivirus_enabled\\\":\\\"$av_name\\\",\\\"antivirus_active\\\":$av_act,\\\"threat_level\\\":\\\"$t_lvl\\\",\\\"top_cpu_procs\\\":\\\"$top_p\\\",\\\"malware_audit\\\":$malware_audit}\";"
            )
        elif cat == "LOGS":
            telemetry_sh = (
                "failed=$(systemctl --failed --no-legend 2>/dev/null | awk '{print $2}' | tr -d '\"\\\\\\r\\n' | tr '\\n' ',' | sed 's/,$//');"
                "disk=$(df -h / 2>/dev/null | awk 'NR==2 {print $5}' | tr -d '\"\\\\\\r\\n');"
                "t=\"{\\\"failed_services\\\":\\\"$failed\\\",\\\"disk_usage\\\":\\\"$disk\\\"}\";"
            )
        else:
            # ANALISIS COMPLETO (Consolidated Full Suite)
            telemetry_sh = (
                "cpu=$(top -bn1 2>/dev/null | grep 'Cpu(s)' | awk '{print $2 + $4}' | cut -d'.' -f1 | tr -d '\"\\\\\\r\\n');"
                "ram=$(free 2>/dev/null | grep Mem | awk '{printf(\"%.1f\", $3/$2 * 100.0)}' | tr -d '\"\\\\\\r\\n');"
                "def_rt=$(ip -4 route show default 2>/dev/null | grep -v '10.0.0.' | head -n 1);"
                "if [ -z \"$def_rt\" ]; then def_rt=$(ip -4 route show default 2>/dev/null | head -n 1); fi;"
                "gw=$(echo \"$def_rt\" | awk '{for(i=1;i<=NF;i++) if($i==\"via\") print $(i+1); else if($i==\"default\" && $(i+1)!=\"dev\") print $3}' | head -n 1 | tr -d '\"\\\\\\r\\n');"
                "iface=$(echo \"$def_rt\" | awk '{for(i=1;i<=NF;i++) if($i==\"dev\") print $(i+1)}' | head -n 1 | tr -d '\"\\\\\\r\\n');"
                "ip=$(ip -4 addr show dev \"$iface\" 2>/dev/null | grep inet | awk '{print $2}' | head -n 1 | tr -d '\"\\\\\\r\\n');"
                "if [ -z \"$ip\" ]; then ip=$(ip -4 addr show up 2>/dev/null | grep -v '127.0.0.1' | grep inet | awk '{print $2}' | head -n 1 | tr -d '\"\\\\\\r\\n'); fi;"
                "mac=$(cat /sys/class/net/$iface/address 2>/dev/null | tr -d '\"\\\\\\r\\n');"
                "ping_gw=$( [ -n \"$gw\" ] && ping -c 2 -W 1 \"$gw\" >/dev/null 2>&1 && echo true || echo false );"
                "osi=\"{\\\"l3_network\\\":{\\\"ip\\\":\\\"$ip\\\",\\\"gateway\\\":\\\"$gw\\\",\\\"ping_gateway\\\":$ping_gw},\\\"l2_datalink\\\":{\\\"interface\\\":\\\"$iface\\\",\\\"mac\\\":\\\"$mac\\\"}}\";"
                "t=\"{\\\"cpu_percent\\\":${cpu:-0},\\\"ram_percent\\\":${ram:-0},\\\"ip\\\":\\\"$ip\\\",\\\"gateway\\\":\\\"$gw\\\",\\\"mac\\\":\\\"$mac\\\",\\\"ping_gateway\\\":$ping_gw,\\\"hardware\\\":$hw,\\\"osi_network\\\":$osi}\";"
            )

        hw_sh = (
            "mfr=$(cat /sys/class/dmi/id/sys_vendor 2>/dev/null || cat /proc/cpuinfo 2>/dev/null | grep -m1 'Hardware' | cut -d: -f2 | xargs || echo 'Linux');"
            "model=$(cat /sys/class/dmi/id/product_name 2>/dev/null || cat /proc/device-tree/model 2>/dev/null | tr -d '\\0' || echo 'Host');"
            "serial=$(cat /sys/class/dmi/id/product_serial 2>/dev/null || echo 'N/A');"
            "os_name=$(cat /etc/os-release 2>/dev/null | grep -m1 '^PRETTY_NAME=' | cut -d= -f2 | tr -d '\"\\\\\\r\\n' || echo 'Linux');"
            "kernel=$(uname -r 2>/dev/null | tr -d '\"\\\\\\r\\n');"
            "cpu_m=$(lscpu 2>/dev/null | grep 'Model name' | cut -d: -f2 | xargs || cat /proc/cpuinfo 2>/dev/null | grep -m1 'model name' | cut -d: -f2 | xargs || echo 'CPU');"
            "ram_t=$(free -m 2>/dev/null | awk '/Mem:/ {printf(\"%.1f\", $2/1024)}' || echo '0');"
            "ram_f=$(free -m 2>/dev/null | awk '/Mem:/ {printf(\"%.1f\", $7/1024)}' || echo '0');"
            "hw=\"{\\\"manufacturer\\\":\\\"$mfr\\\",\\\"model\\\":\\\"$model\\\",\\\"serial\\\":\\\"$serial\\\",\\\"os_name\\\":\\\"$os_name\\\",\\\"kernel\\\":\\\"$kernel\\\",\\\"cpu_model\\\":\\\"$cpu_m\\\",\\\"ram_total_gb\\\":$ram_t,\\\"ram_free_gb\\\":$ram_f}\";"
        )

        script = (
            f"hn=$(hostname 2>/dev/null | tr -d '\"\\\\\\r\\n' || echo 'linux-client');"
            f"{hw_sh}"
            f"osi=\"{{}}\";"
            f"hw_audit=\"{{}}\";"
            f"malware_audit=\"{{}}\";"
            f"{telemetry_sh}"
            f"p=\"{{\\\"os_type\\\":\\\"linux\\\",\\\"category\\\":\\\"{cat}\\\",\\\"hostname\\\":\\\"$hn\\\",\\\"hardware\\\":$hw,\\\"hardware_audit\\\":$hw_audit,\\\"malware_audit\\\":$malware_audit,\\\"osi_network\\\":$osi,\\\"telemetry\\\":$t}}\";"
            f"curl -s -m 10 -X POST -H 'Content-Type: application/json' -d \"$p\" {endpoint_uri} >/dev/null 2>&1 || wget -q --timeout=10 --header='Content-Type: application/json' --post-data=\"$p\" -O- {endpoint_uri} >/dev/null 2>&1"
        )
        return script

    @classmethod
    def get_bash_payload(cls, category: str, server_url: str = "http://10.0.0.1:8000") -> str:
        """
        Returns a compact micro-stager (<150 chars) that downloads and executes
        the full Bash telemetry script from REI's local web server in the background.
        """
        cat = cls.normalize_category(category)
        url = f"{server_url.rstrip('/')}/l/{cat}"
        return f"(curl -s -m 5 {url}||wget -qO- {url})|sh >/dev/null 2>&1 & disown"


class LinuxHIDPlugin(IDiagnosticPlugin):
    """
    Decoupled plugin that injects Linux diagnostic commands via Rubber Ducky
    and waits for telemetry report over HTTP.
    """

    def __init__(
        self,
        category: str = "ANALISIS COMPLETO",
        keyboard_layout: str = "es",
        server_url: str = "http://10.0.0.1:8000",
        timeout_seconds: float = 25.0,
        injector: Optional[DuckyInjector] = None,
        web_server: Optional[Any] = None,
    ):
        self._category = category.upper()
        self._layout = keyboard_layout.lower()
        self._server_url = server_url
        self._timeout_seconds = timeout_seconds
        self._injector = injector or DuckyInjector()
        self._web_server = web_server

    @property
    def id(self) -> str:
        return "diag_linux_hid"

    @property
    def name(self) -> str:
        return f"LINUX {self._category[:12]} ({self._layout.upper()})"

    @property
    def category(self) -> str:
        return "ENDPOINTS"

    def set_context(self, category: str, layout: str, web_server: Optional[Any] = None) -> None:
        """Dynamically updates execution context."""
        self._category = category.upper()
        self._layout = layout.lower()
        if web_server:
            self._web_server = web_server
            if hasattr(web_server, "base_url") and web_server.base_url:
                self._server_url = web_server.base_url

    def run(self, **kwargs) -> DiagnosticResult:
        progress_cb: Optional[Callable[[str, float], None]] = kwargs.get("progress_callback")
        start_time = time.monotonic()

        # Step 0: Validate hardware connection before starting
        is_ready, not_ready_details = self._injector.check_ready()
        if not is_ready:
            return DiagnosticResult(
                plugin_name=self.name,
                target_identifier=f"Linux ({self._layout.upper()})",
                status=DiagnosticStatus.FAILED,
                overall_status=Severity.WARNING,
                summary=not_ready_details[0] if not_ready_details else "USB no listo",
                details=not_ready_details,
            )

        # Step 1: Prepare Payload
        if progress_cb:
            progress_cb("Generando payload...", 0.1)

        payload_cmd = LinuxPayloadGenerator.get_bash_payload(
            category=self._category,
            server_url=self._server_url,
        )

        # Capture watermark before HID typing to eliminate race conditions
        watermark = self._web_server.get_report_watermark() if self._web_server else 0

        # Step 2: Inject via USB HID
        if progress_cb:
            progress_cb("Inyectando HID...", 0.3)

        try:
            # Emulate Ctrl + Alt + t to open terminal on Linux Desktop
            self._injector.press_combination("ctrl+alt", "t")
            time.sleep(1.2)

            # Write bash execution string and press ENTER (leading space avoids history)
            self._injector.write_text(f" {payload_cmd}\n", layout=self._layout)
            time.sleep(0.5)
            # Ensure process is disowned and close terminal cleanly
            self._injector.write_text("disown -a 2>/dev/null || true\nexit\n", layout=self._layout)

        except Exception as inj_ex:
            logger.error(f"HID injection failed: {inj_ex}")
            if not self._injector.dry_run:
                err_str = str(inj_ex).lower()
                if "timeout" in err_str or "no responde" in err_str:
                    details = ["Host no acepta HID", "Verifique cable de datos", "Use puerto USB (no PWR)"]
                    summary = "Timeout USB HID"
                elif "desconectado" in err_str:
                    details = ["Cable desconectado", "Conecte puerto USB a PC", "Use cable de datos"]
                    summary = "USB desconectado"
                else:
                    details = [str(inj_ex)[:20], "Verifique conexión USB"]
                    summary = "Fallo de inyección"

                return DiagnosticResult(
                    plugin_name=self.name,
                    target_identifier=f"Linux ({self._layout.upper()})",
                    status=DiagnosticStatus.FAILED,
                    overall_status=Severity.CRITICAL,
                    summary=summary,
                    details=details,
                )

        # Step 3: Await Telemetry over HTTP
        if progress_cb:
            progress_cb("Esperando telemetría...", 0.6)

        report = None
        if self._web_server:
            report = self._web_server.wait_for_report(watermark=watermark, timeout_seconds=self._timeout_seconds)

        # If in dry-run or simulated
        if not report:
            if self._injector.dry_run:
                if progress_cb:
                    progress_cb("Simulando reporte...", 0.85)
                time.sleep(0.5)
                report_data = {
                    "os_type": "LINUX",
                    "category": self._category,
                    "hostname": "linux-client",
                    "hardware": {
                        "manufacturer": "Lenovo",
                        "model": "ThinkPad T14 Gen 2",
                        "serial": "PF2X9Y1Z",
                        "os_name": "Ubuntu 22.04.4 LTS",
                        "kernel": "5.15.0-105-generic",
                        "cpu_model": "AMD Ryzen 5 PRO 5650U with Radeon Graphics",
                        "ram_total_gb": 15.4,
                        "ram_free_gb": 8.7,
                    },
                    "osi_network": {
                        "l7_application": {
                            "dns_servers": "192.168.1.1,1.1.1.1",
                            "dns_ok": True,
                            "http_status": 204,
                            "captive_portal": False,
                        },
                        "l6_l5_session": {
                            "active_tcp_conns": 18,
                        },
                        "l3_network": {
                            "ip": "192.168.1.88/24",
                            "gateway": "192.168.1.1",
                            "ping_gateway": True,
                            "ping_internet": True,
                            "gateway_rtt_ms": "1.2",
                            "mtu": "1500",
                            "traceroute_hops": "1:192.168.1.1;2:10.10.0.1;3:8.8.8.8",
                        },
                        "l2_datalink": {
                            "interface": "wlan0",
                            "mac": "34:cf:f6:12:34:56",
                            "speed": "N/A",
                            "gateway_arp": "00:11:22:33:44:55",
                            "wifi_ssid": "Corp_Lab_WiFi",
                            "wifi_signal": "78",
                        },
                        "l1_physical": {
                            "carrier": "1",
                            "operstate": "up",
                        },
                    },
                    "hardware_audit": {
                        "system": {
                            "manufacturer": "Lenovo",
                            "model": "ThinkPad T14 Gen 2",
                            "serial": "PF2X9Y1Z",
                            "board_mfr": "Lenovo",
                            "board_product": "20W0CTO1WW",
                            "bios_version": "N34ET56W (1.56)",
                            "bios_date": "2023-08-15",
                            "os_name": "Ubuntu 22.04.4 LTS",
                            "kernel": "5.15.0-105-generic",
                        },
                        "cpu": {
                            "name": "AMD Ryzen 5 PRO 5650U with Radeon Graphics",
                            "cores": 6,
                            "threads": 12,
                            "max_mhz": 4200,
                            "current_mhz": 1800,
                            "load_pct": 12.4,
                            "temp_c": 44.5,
                        },
                        "memory": {
                            "total_gb": 15.4,
                            "free_gb": 8.7,
                            "used_gb": 6.7,
                            "usage_pct": 38.0,
                        },
                        "storage": {
                            "physical_drives": [
                                {"name": "nvme0n1", "model": "SAMSUNG_MZVLB512HBJQ", "size": "476.9G", "rotational": False, "bus": "nvme"}
                            ],
                            "volumes": [
                                {"drive": "/dev/nvme0n1p2", "size": "470G", "used": "142G", "free": "304G", "use_pct": "32%", "mount": "/"}
                            ],
                        },
                        "graphics": [
                            {"name": "Advanced Micro Devices, Inc. [AMD/ATI] Cezanne [Radeon Vega Series / Radeon Vega Mobile Series]"}
                        ],
                        "battery": {
                            "present": True,
                            "charge_pct": 88,
                            "status": "Discharging",
                        },
                    },
                    "malware_audit": {
                        "defenses": {
                            "antivirus_name": "ClamAV",
                            "antivirus_active": True,
                            "firewall_enabled": True,
                            "security_module": "AppArmor",
                        },
                        "suspicious_processes": [],
                        "persistence": [
                            {"type": "Systemd User", "name": "app.service", "path": "app.service", "location": "~/.config/systemd"}
                        ],
                        "network_c2": {
                            "listening_ports": [
                                {"port": 22}
                            ],
                            "established_connections": [],
                            "hosts_file_hijack": False,
                        },
                        "recent_artifacts": [],
                        "threat_score": 0,
                        "threat_level": "BAJO",
                    },
                    "telemetry": {
                        "cpu_percent": 12.4,
                        "ram_percent": 38.0,
                        "ip": "192.168.1.88/24",
                        "gateway": "192.168.1.1",
                        "mac": "34:cf:f6:12:34:56",
                        "ping_gateway": True,
                        "ping_internet": True,
                        "antivirus_enabled": "ClamAV",
                        "threat_level": "BAJO",
                    },
                }
                rep_id = (
                    self._web_server.store_local_report(
                        os_type="LINUX",
                        category=self._category,
                        hostname="linux-client",
                        telemetry=report_data["telemetry"],
                        hardware=report_data["hardware"],
                        hardware_audit=report_data["hardware_audit"],
                        malware_audit=report_data["malware_audit"],
                        osi_network=report_data["osi_network"],
                    )
                    if self._web_server
                    else "mock-linux"
                )
                report = type("StoredReportMock", (), {
                    "report_id": rep_id,
                    "hostname": "linux-client",
                    "os_type": "LINUX",
                    "category": self._category,
                    "hardware": report_data["hardware"],
                    "hardware_audit": report_data["hardware_audit"],
                    "malware_audit": report_data["malware_audit"],
                    "osi_network": report_data["osi_network"],
                    "telemetry": report_data["telemetry"],
                    "overall_status": "OK",
                    "ai_analysis": None,
                })()

        if not report:
            return DiagnosticResult(
                plugin_name=self.name,
                target_identifier=f"Linux ({self._layout.upper()})",
                status=DiagnosticStatus.FAILED,
                overall_status=Severity.WARNING,
                summary="Timeout esperando reporte",
                details=["No se recibió HTTP POST", "Verifique cable y red RNDIS/ECM"],
            )

        # Step 4: Build Metrics & Result
        if progress_cb:
            progress_cb("Procesando métricas...", 0.95)

        metrics: List[DiagnosticMetric] = []
        details: List[str] = [
            f"Host: {getattr(report, 'hostname', 'Linux')[:14]}",
            f"Cat:  {self._category[:14]}",
        ]

        hw_data = getattr(report, "hardware", {}) or getattr(report, "telemetry", {}).get("hardware", {})
        if hw_data:
            mfr = hw_data.get("manufacturer") or ""
            model = hw_data.get("model") or "PC"
            hw_str = f"{mfr} {model}".strip()[:18]
            metrics.append(DiagnosticMetric(name="Equipo", value=hw_str, status=Severity.INFO))
            details.append(f"Eq:   {hw_str[:14]}")

        osi_data = getattr(report, "osi_network", {}) or getattr(report, "telemetry", {}).get("osi_network", {})
        hw_audit = getattr(report, "hardware_audit", {}) or getattr(report, "telemetry", {}).get("hardware_audit", {})
        malware_audit = getattr(report, "malware_audit", {}) or getattr(report, "telemetry", {}).get("malware_audit", {})
        t_data = getattr(report, "telemetry", {})
        overall_severity = Severity.OK

        # Dedicated Hardware category processing
        if (self._category == "HARDWARE" or "HARDWARE" in self._category) and hw_audit:
            cpu_info = hw_audit.get("cpu", {})
            c_load = cpu_info.get("load_pct") or t_data.get("cpu_percent") or 0
            c_cores = cpu_info.get("cores")
            c_core_str = f" ({c_cores}C)" if c_cores else ""
            c_temp = cpu_info.get("temp_c") or t_data.get("cpu_temp_c")
            c_sev = Severity.CRITICAL if float(c_load) > 90 else (Severity.WARNING if float(c_load) > 75 else Severity.OK)
            if c_sev != Severity.OK and overall_severity != Severity.CRITICAL:
                overall_severity = c_sev
            metrics.append(DiagnosticMetric(name="CPU", value=f"{c_load}%{c_core_str}", status=c_sev))

            if c_temp and float(c_temp) > 0:
                t_sev = Severity.CRITICAL if float(c_temp) > 85 else (Severity.WARNING if float(c_temp) > 75 else Severity.OK)
                if t_sev != Severity.OK and overall_severity != Severity.CRITICAL:
                    overall_severity = t_sev
                metrics.append(DiagnosticMetric(name="Temp CPU", value=f"{c_temp}°C", status=t_sev))

            mem_info = hw_audit.get("memory", {})
            m_tot = mem_info.get("total_gb") or hw_data.get("ram_total_gb") or 0
            m_used = mem_info.get("used_gb") or 0
            m_pct = mem_info.get("usage_pct") or t_data.get("ram_percent") or 0
            m_sev = Severity.CRITICAL if float(m_pct) > 90 else (Severity.WARNING if float(m_pct) > 80 else Severity.OK)
            if m_sev != Severity.OK and overall_severity != Severity.CRITICAL:
                overall_severity = m_sev
            metrics.append(DiagnosticMetric(name="RAM", value=f"{m_used}/{m_tot} GB ({m_pct}%)", status=m_sev))

            storage_info = hw_audit.get("storage", {})
            p_drives = storage_info.get("physical_drives", [])
            vols = storage_info.get("volumes", [])
            for v in vols:
                drv = v.get("mount") or v.get("drive") or "Raiz"
                use_p_str = str(v.get("use_pct", "0%")).replace("%", "")
                try:
                    use_p = float(use_p_str)
                except ValueError:
                    use_p = 0
                f_str = v.get("free", "N/A")
                d_sev = Severity.CRITICAL if use_p > 95 else (Severity.WARNING if use_p > 90 else Severity.OK)
                if d_sev != Severity.OK and overall_severity != Severity.CRITICAL:
                    overall_severity = d_sev
                metrics.append(DiagnosticMetric(name=f"Disco {drv}", value=f"{f_str} lib ({use_p}%)", status=d_sev))

            bat_info = hw_audit.get("battery")
            if bat_info and isinstance(bat_info, dict) and bat_info.get("present"):
                ch_pct = bat_info.get("charge_pct", 0)
                metrics.append(DiagnosticMetric(name="Batería", value=f"{ch_pct}%", status=Severity.OK))

            details = [
                f"Host: {getattr(report, 'hostname', 'Linux')[:14]}",
                f"CPU: {c_load}%{c_core_str}"[:14],
                f"RAM: {m_used}/{m_tot}GB ({m_pct}%)"[:14],
                f"DSK: {len(vols)} vol {'TMP:'+str(c_temp)+'C' if c_temp else ''}"[:14],
            ]
        # Dedicated Malware category processing
        elif (self._category == "MALWARE" or "MALWARE" in self._category) and malware_audit:
            def_info = malware_audit.get("defenses", {})
            av_name = def_info.get("antivirus_name") or t_data.get("antivirus_enabled") or "Ninguno"
            av_act = def_info.get("antivirus_active") if "antivirus_active" in def_info else (av_name != "Ninguno")
            av_sev = Severity.OK if av_act else Severity.CRITICAL
            if av_sev == Severity.CRITICAL:
                overall_severity = Severity.CRITICAL
            metrics.append(DiagnosticMetric(name="Antivirus", value=f"{str(av_name)[:12]} ({'OK' if av_act else 'OFF'})", status=av_sev))

            th_lvl = malware_audit.get("threat_level", "BAJO")
            th_score = malware_audit.get("threat_score", 0)
            th_sev = Severity.CRITICAL if th_lvl == "CRITICO" else (Severity.WARNING if th_lvl == "MEDIO" else Severity.OK)
            if th_sev != Severity.OK and overall_severity != Severity.CRITICAL:
                overall_severity = th_sev
            metrics.append(DiagnosticMetric(name="Nivel Amenaza", value=f"{th_lvl} ({th_score}pts)", status=th_sev))

            susp_p = malware_audit.get("suspicious_processes", [])
            p_sev = Severity.CRITICAL if len(susp_p) > 0 else Severity.OK
            if p_sev != Severity.OK and overall_severity != Severity.CRITICAL:
                overall_severity = p_sev
            metrics.append(DiagnosticMetric(name="Proc Sospechosos", value=str(len(susp_p)), status=p_sev))

            persist = malware_audit.get("persistence", [])
            metrics.append(DiagnosticMetric(name="Persistencias", value=str(len(persist)), status=Severity.INFO if len(persist) < 5 else Severity.WARNING))

            net_c2 = malware_audit.get("network_c2", {})
            susp_conns = [c for c in net_c2.get("established_connections", []) if isinstance(c, dict) and c.get("suspicious")]
            c2_sev = Severity.CRITICAL if len(susp_conns) > 0 else Severity.OK
            if c2_sev != Severity.OK:
                overall_severity = Severity.CRITICAL
            metrics.append(DiagnosticMetric(name="Conexiones C2", value=str(len(susp_conns)), status=c2_sev))

            details = [
                f"Host: {getattr(report, 'hostname', 'Linux')[:14]}",
                f"AV: {'OK' if av_act else 'DESACTIVADO'}"[:14],
                f"P:{len(susp_p)} Pers:{len(persist)} C2:{len(susp_conns)}"[:14],
                f"Riesgo: {th_lvl}"[:14],
            ]
        else:
            # General / Network / Malware metrics
            cpu = t_data.get("cpu_percent")
            if cpu is not None:
                c_val = f"{cpu}%"
                c_sev = Severity.CRITICAL if float(cpu) > 90 else (Severity.WARNING if float(cpu) > 75 else Severity.OK)
                metrics.append(DiagnosticMetric(name="Uso CPU", value=c_val, status=c_sev))
                if len(details) < 4:
                    details.append(f"CPU:  {c_val}")

            ram = t_data.get("ram_percent")
            if ram is not None:
                r_val = f"{ram}%"
                r_sev = Severity.CRITICAL if float(ram) > 90 else Severity.OK
                metrics.append(DiagnosticMetric(name="Uso RAM", value=r_val, status=r_sev))

            # Network OSI Metrics
            l3 = osi_data.get("l3_network", {}) if isinstance(osi_data, dict) else {}
            l7 = osi_data.get("l7_application", {}) if isinstance(osi_data, dict) else {}
            l2 = osi_data.get("l2_datalink", {}) if isinstance(osi_data, dict) else {}

            ip_val = l3.get("ip") or t_data.get("ip")
            if ip_val:
                metrics.append(DiagnosticMetric(name="IP Host", value=str(ip_val)[:15], status=Severity.INFO))
                if len(details) < 4:
                    details.append(f"IP:   {str(ip_val)[:14]}")

            gw_val = l3.get("gateway") or t_data.get("gateway")
            gw_ping = l3.get("ping_gateway") if "ping_gateway" in l3 else t_data.get("ping_gateway")
            if gw_val:
                gw_status = Severity.OK if gw_ping else Severity.CRITICAL
                metrics.append(DiagnosticMetric(name="Gateway", value=f"{gw_val} ({'OK' if gw_ping else 'FAIL'})", status=gw_status))

            mac_val = l2.get("mac") or t_data.get("mac")
            if mac_val:
                metrics.append(DiagnosticMetric(name="MAC", value=str(mac_val), status=Severity.INFO))

            dns_ok = l7.get("dns_ok")
            if dns_ok is not None:
                dns_status = Severity.OK if dns_ok else Severity.CRITICAL
                metrics.append(DiagnosticMetric(name="DNS Status", value="Resuelto" if dns_ok else "Fallo", status=dns_status))

        elapsed_ms = int((time.monotonic() - start_time) * 1000)

        return DiagnosticResult(
            plugin_name=self.name,
            target_identifier=f"Linux ({getattr(report, 'hostname', 'Host')})",
            execution_time_ms=elapsed_ms,
            status=DiagnosticStatus.SUCCESS,
            overall_status=overall_severity,
            summary=f"Diagnóstico {self._category} OK" if overall_severity != Severity.CRITICAL else f"Diagnóstico {self._category} CON ALERTAS",
            details=details[:4],
            metrics=metrics,
            raw_output=json.dumps(t_data),
            metadata={"report_id": getattr(report, "report_id", "latest"), "os_type": "LINUX"},
        )
