#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
#  ------------------------------------------------------------------------------
#  Program to monitor the following parameters of a Raspberry Pi and send the
#  the data via MQTT to Home Assistant
#  - General data of the Raspberry Pi, like
# 	   - model (e.g. RPI 3B+, RPI 4B, RPI ZeroW)
#    - running operating system (release and version)
#    - network interface(s) with MAC and IP addresses
#    - hostname, fqdn
#    - number of CPU core(s)
#    - clock speed of CPU (min|max)
#    - architecture
#    - mounted filesystem(s)
# 		 - memory installed
#    - drive size installed
#  - Operating data of the Raspberry Pi, like
#    - date of last update and upgrade of OS
#    - uptime
#    - Temperature CPU
#    - Temperature GPU (only, if command vcgencmd is available on the Raspberry Pi)
#    - % of memory used
#    - % of used drive space
#    - CPU load (1m and 5m)
#    - system security status ("safe" if OS update less than 1 day old (default),
#      and upgrade < 7 days, otherwise "unsafe", thresholds can be set in "config.ini"
# 	   file, ranges are hardcoded)


#  --------------------------
#  import necessary libraries
#
import _thread
from datetime import datetime
from tzlocal import get_localzone
import sys
import ssl
import json
import os
import os.path
import argparse
import threading
import subprocess
from time import sleep, localtime, strftime
from collections import OrderedDict

# from colorama import init as colorama_init
from colorama import Fore, Style
from configparser import ConfigParser
from unidecode import unidecode
import paho.mqtt.client as mqtt
import sdnotify
import rpi

script_version = "1.7.1"
script_name = "rpi-monitor.py"
script_info = "{} v{}".format(script_name, script_version)
project_name = "rpi-monitor"
project_url = "https://github.com/ufankhau/rpi-monitor"

local_tz = get_localzone()


#  ----------------
#  Helper Functions
#
#  Logging
def print_line(
    text,
    error=False,
    warning=False,
    info=False,
    verbose=False,
    debug=False,
    console=True,
    sd_notify=False,
):
    timestamp = strftime("%Y-%m-%d %H:%M:%S", localtime())
    if console:
        if error:
            print(
                "{}{}[{}] {}{}{}".format(
                    Fore.RED,
                    Style.BRIGHT,
                    timestamp,
                    Style.RESET_ALL,
                    text,
                    Style.RESET_ALL,
                ),
                file=sys.stderr,
            )
        elif warning:
            print(
                "{}[{}] {}{}{}".format(
                    Fore.YELLOW, timestamp, Style.RESET_ALL, text, Style.RESET_ALL
                )
            )
        elif info or verbose:
            if opt_verbose:
                print(
                    "{}[{}] {}{}{}".format(
                        Fore.GREEN, timestamp, Fore.YELLOW, text, Style.RESET_ALL
                    )
                )
        elif debug:
            if opt_debug:
                print(
                    "{}[{}] - (DBG): {}{}".format(
                        Fore.CYAN, timestamp, text, Style.RESET_ALL
                    )
                )
        else:
            print(
                "{}[{}] {}{}{}".format(
                    Fore.GREEN, timestamp, Style.RESET_ALL, text, Style.RESET_ALL
                )
            )
    timestamp_sd = strftime("%b %d %H:%M:%S", localtime())
    if sd_notify:
        sd_notifier.notify("STATUS={} - {}.".format(timestamp_sd, unidecode(text)))


#  check, that python 3 is available
if False:
    # will be caught by python 2.7 to be illegal syntax
    print_line(
        "Sorry, this script requires a python 3 runtime environment.", file=sys.stderr
    )
    os._exit(1)

#  Initiate variables
opt_debug = False
opt_verbose = False

#  Systemd Service Notifications - https://github.com/bb4242/sdnotify
sd_notifier = sdnotify.SystemdNotifier()


#  --------
#  argparse
#
arg = argparse.ArgumentParser(
    description=project_name, epilog="For further details see: " + project_url
)
arg.add_argument(
    "-v", "--verbose", help="increase output verbosity", action="store_true"
)
arg.add_argument("-d", "--debug", help="show debug output", action="store_true")
arg.add_argument(
    "-s", "--stall", help="TEST: report only the first time", action="store_true"
)
arg.add_argument(
    "-c",
    "--config_dir",
    help="set directory where config.ini is located",
    default=sys.path[0],
)
parse_args = arg.parse_args()

config_dir = parse_args.config_dir
opt_debug = parse_args.debug
opt_verbose = parse_args.verbose
opt_stall = parse_args.stall

print_line(script_info, info=True)
if opt_verbose:
    print_line("Verbose enabled", info=True)
if opt_debug:
    print_line("Debug enabled", debug=True)
if opt_stall:
    print_line("Test: Stall (no-re-reporting) enabled", debug=True)


#  -----------------------------------------------------------------
#  MQTT - Callback Functions that are Called in Response to an Event
#
mqtt_client_connected = False
print_line(
    "* init mqtt_client_connected=[{}]".format(mqtt_client_connected), debug=True
)
mqtt_client_should_attempt_reconnect = True


def on_connect(client, userdata, flags, rc):
    """
    Callback function triggered by MQTT client in a connection event
    """
    global mqtt_client_connected
    print_line("on_connect() - client, userdata, flags, rc", debug=True)
    print_line(
        "Data received (client, userdata, flags, rc): ({}, {}, {}, {})".format(
            client, userdata, flags, rc
        ),
        debug=True,
    )
    if rc == 0:
        print_line("* MQTT connection established", console=True, sd_notify=True)
        print_line("")  #  blank line
        mqtt_client_connected = True
        print_line(
            "on_connect() mqtt_client_connected = [{}]".format(mqtt_client_connected),
            debug=True,
        )

        # commands subscription
        if len(commands) > 0:
            mqtt_client.subscribe("{}/+".format(command_base_topic))
            print_line(
                "MQTT subscription to {}/+ enabled".format(command_base_topic),
                console=True,
                sd_notify=True,
            )
        else:
            print_line(
                "MQTT subscription to {}/+ disabled".format(command_base_topic),
                console=True,
                sd_notify=True,
            )

    else:
        print_line(
            "! Connection error with result code {} - {}".format(
                str(rc), mqtt.connack_string(rc)
            ),
            error=True,
        )
        print_line(
            "MQTT Connection error with result code {} - {}".format(
                str(rc), mqtt.connack_string(rc)
            ),
            error=True,
            sd_notify=True,
        )
        mqtt_client_connected = False
        print_line(
            "on_connected() mqtt_client_connected = [{}]".format(mqtt_client_connected),
            debug=True,
            error=True,
        )
        # kill main thread
        os._exit(1)


def on_disconnect(client, userdata, mid):
    """
    Callback function triggered by MQTT client in a disconnect event
    """
    global mqtt_client_connected
    mqtt_client_connected = False
    print_line("* MQTT connection lost", console=True, sd_notify=True)
    print_line(
        "on_disconnect() mqtt_client_connected = [{}]".format(mqtt_client_connected),
        debug=True,
    )
    pass


def on_publish(client, userdata, mid):
    """
    Callback function triggered by MQTT broker in a PUBLISH event
    """
    print_line("* Data successfully published.", debug=True)
    print_line(
        "(client | userdata | mid): {} | {} | {}".format(client, userdata, mid),
        debug=True,
    )
    pass


def on_subscribe(client, userdata, mid, granted_qos):
    """ """
    print_line(
        "on_subscribe() - {} - {}".format(str(mid), str(granted_qos)),
        debug=True,
        sd_notify=True,
    )


def on_message(client, userdata, message):
    """
    Callback function triggered by MQTT client in a event receiving a MESSAGE
    from the broker
    """
    sh_cmd_loc = rpi.get_command_location("sh")
    if sh_cmd_loc != "":
        payload = message.payload.decode("utf-8")
        command = message.topic.split("/")[-1]
        print_line(
            "on_message() topic = [{}] payload = [{}] command = [{}]".format(
                message.topic, message.payload, command
            ),
            console=True,
            sd_notify=True,
            debug=True,
        )

        if command != "status":
            if command in commands:
                print_line(
                    '- Command "{}" Received - Run {} {} -'.format(
                        command, commands[command], payload
                    ),
                    console=True,
                    debug=True,
                )
                pHandle = subprocess.Popen(
                    [sh_cmd_loc, "-c", commands[command].format(payload)]
                )
                _, errors = pHandle.communicate()
                if errors:
                    print_line(
                        "- Command exec says: errors=[{}]".format(errors),
                        console=True,
                        debug=True,
                    )
            else:
                print_line("* Invalid Command received.", error=True)

    else:
        print_line("* Failed to locate shell Command!", error=True)
        os._exit(1)


#  -----------------------
#  Load Configuration File
#
commands = OrderedDict([])
config = ConfigParser(delimiters=("=",), inline_comment_prefixes=("#"))
config.optionxform = str
try:
    with open(os.path.join(config_dir, "config.ini")) as config_file:
        config.read_file(config_file)
except IOError:
    print_line('No configuration file "config.ini"', error=True, sd_notify=True)
    sys.exit(1)


#  Read [Commands] Section of config.ini, if exists
if config.has_section("Commands"):
    command_set = dict(config["Commands"].items())
    if len(command_set) > 0:
        commands.update(command_set)


#  Read [Daemon] Section of config.ini
daemon_enabled = config["Daemon"].getboolean("enabled", True)

#  default domain when hostname -f doesn't return it
default_domain = "home"
fallback_domain = config["Daemon"].get("fallback_domain", default_domain).lower()

#  reporting interval of Raspberry values in minutes [1 - 20], [default: 3]
min_reporting_interval_in_minutes = 1
max_reporting_interval_in_minutes = 20
default_reporting_interval_in_minutes = 3
reporting_interval_in_minutes = config["Daemon"].getint(
    "reporting_interval_in_minutes", default_reporting_interval_in_minutes
)

#  period in hours between checking for pending updates for the OS running on
#  the Raspberry Pi, [default: 6]
min_timespan_update_check_in_hours = 4
max_timespan_update_check_in_hours = 24
default_timespan_update_check_in_hours = 6
timespan_update_check_in_hours = config["Daemon"].getint(
    "timespan_update_check_in_hours", default_timespan_update_check_in_hours
)
timespan_update_check_in_seconds = timespan_update_check_in_hours * 60 * 60


#  Read [MQTT] Section of config.ini
default_base_topic = "home/nodes"
base_topic = config["MQTT"].get("base_topic", default_base_topic).lower()

default_device_name = "rpi"
device_name = config["MQTT"].get("device_name", default_device_name).lower()

#  by default Home Assistant listens to /homeassistant
default_discovery_prefix = "homeassistant"
discovery_prefix = (
    config["MQTT"].get("discovery_prefix", default_discovery_prefix).lower()
)


#  Check Configuration
if (timespan_update_check_in_hours < min_timespan_update_check_in_hours) or (
    timespan_update_check_in_hours > max_timespan_update_check_in_hours
):
    print_line(
        'ERROR: invalid "OS_update_check_in_hours" found in configuration file:'
        + ' "config.ini"! Must be within range [{}-{}]. Fix and try again ....'.format(
            min_timespan_update_check_in_hours, max_timespan_update_check_in_hours
        ),
        error=True,
        sd_notify=True,
    )
    sys.exit(1)

if (reporting_interval_in_minutes < min_reporting_interval_in_minutes) or (
    reporting_interval_in_minutes > max_reporting_interval_in_minutes
):
    print_line(
        'ERROR: invalid "reporting_interval_in_minutes" found in configuration'
        + ' file: "config.ini"! Must be [{}-{}] Fix and try again .... aborting'.format(
            min_reporting_interval_in_minutes, max_reporting_interval_in_minutes
        ),
        error=True,
        sd_notify=True,
    )
    sys.exit(1)

#  ensure config.ini file has a [MQTT] section
if not config["MQTT"]:
    print_line(
        'ERROR: No MQTT settings found in configuration file "config.ini"! \
		Fix and try again ... aborting',
        error=True,
        sd_notify=True,
    )
    sys.exit(1)

print_line("Configuration accepted", debug=True, sd_notify=True)


#  -----------------
#  List of Constants
ALIVE_TIMEOUT_IN_SECONDS = 60
TIMER_INTERRUPT = -1
TEST_INTERRUPT = -2

mem_units = {0: "kB", 1: "MB", 2: "GB", 3: "TB"}


#  --------------------------------
#  Raspberry Pi Variables Monitored
#
#  ... with static content
rpi_cpu_model = {}
rpi_fqdn = ""
rpi_drive_mounted = ""
rpi_drive_size = 0
rpi_drive_size_unit = ""
rpi_hostname = ""
rpi_mac_address = ""
rpi_model = ""
rpi_mqtt_script = script_info.replace(".py", "")
rpi_network_interfaces = OrderedDict()
rpi_number_of_cpu_cores = 0
rpi_os_bit_length = 0
rpi_os_release = ""
rpi_os_version = ""
rpi_memory_installed = 0
rpi_memory_installed_unit = ""

#  ... with dynamic content
rpi_cpu_load_1m = 0.0
rpi_cpu_load_5m = 0.0
rpi_cpu_load_15m = 0.0
rpi_cpu_temp = 0.0
rpi_drive_used = 0
rpi_gpu_temp = 0.0
rpi_last_update_run = 0
rpi_os_nbr_of_pending_updates = 0
rpi_os_pending_updates_content = OrderedDict()
rpi_os_upgradable = False
rpi_ram_used = 0
rpi_uptime = ""


#  ----------------
#  Load Static Data
#
rpi_hostname, rpi_fqdn = rpi.get_hostname()
if device_name == default_device_name:
    device_name = "rpi-{}".format(rpi_hostname)
print_line("rpi_hostname = [{}]".format(rpi_hostname), debug=True)
print_line("rpi_fqdn = [{}]".format(rpi_fqdn), debug=True)
rpi_model = rpi.get_device_model()
print_line("rpi_model = [{}]".format(rpi_model), debug=True)
rpi_cpu_model = rpi.get_device_cpu_info()
print_line("rpi_cpu_model = [{}]".format(rpi_cpu_model), debug=True)
rpi_number_of_cpu_cores = rpi_cpu_model["Core(s)"]
print_line("rpi_nbrCores = [{}]".format(rpi_number_of_cpu_cores), debug=True)
rpi_os_bit_length = rpi.get_os_bit_length()
print_line("rpi_os_bit_length = [{}]".format(rpi_os_bit_length), debug=True)
rpi_os_release = "{} | {}-bit".format(rpi.get_os_release(), rpi_os_bit_length)
print_line("rpi_os_release = [{}]".format(rpi_os_release), debug=True)
rpi_os_version = rpi.get_os_version()
print_line("rpi_os_version = [{}]".format(rpi_os_version), debug=True)
rpi_memory_installed, unit = rpi.get_device_memory_installed()
rpi_memory_installed_unit = mem_units[unit]
print_line(
    "rpi_mem_installed = [{}{}]".format(
        rpi_memory_installed, rpi_memory_installed_unit
    ),
    debug=True,
)
rpi_drive_size, unit = rpi.get_device_drive_size()
rpi_drive_size_unit = mem_units[unit]
print_line(
    "rpi_device_drive_size = [{}{}]".format(rpi_drive_size, rpi_drive_size_unit),
    debug=True,
)
drive_mounted = rpi.get_drives_mounted()
print_line("fs_mounted = [{}]".format(drive_mounted), debug=True)
for line in drive_mounted:
    if line != "none":
        line_parts = line.split(",")
        rpi_drive_mounted[line_parts[0]] = "-\> {}".format(line_parts[1])
    else:
        rpi_drive_mounted = "none"
print_line("rpi_drive_mounted = [{}]".format(rpi_drive_mounted), debug=True)
rpi_network_interfaces, rpi_mac_address = rpi.get_network_interfaces()
print_line("rpi_interfaces = [{}]".format(rpi_network_interfaces), debug=True)
print_line("rpi_mac_address = [{}]".format(rpi_mac_address), debug=True)

rpi_timestamp_of_last_os_upgrade = strftime(
    "%Y-%m-%d %H:%M:%S", localtime(rpi.get_timestamp_of_last_os_upgrade_in_seconds())
)
print_line(
    "rpi_timestamp_of_last_os_upgrade = [{}]".format(rpi_timestamp_of_last_os_upgrade),
    debug=True,
)


#  ---------------------------------------------------------
#  timer and timer functions for ALIVE MQTT notices handling
#
def publish_alive_status():
    print_line("- SEND: yes, still alive - ", debug=True)
    mqtt_client.publish(lwt_sensor_topic, payload=lwt_online_val, retain=False)
    mqtt_client.publish(lwt_binary_sensor_topic, payload=lwt_online_val, retain=False)
    mqtt_client.publish(lwt_command_topic, payload=lwt_online_val, retain=False)


def publish_shutting_down_status():
    print_line("- SEND: shutting down -", debug=True)
    mqtt_client.publish(lwt_sensor_topic, payload=lwt_offline_val, retain=False)
    mqtt_client.publish(lwt_binary_sensor_topic, payload=lwt_offline_val, retain=False)
    mqtt_client.publish(lwt_command_topic, payload=lwt_offline_val, retain=False)


def mqtt_alive_handler():
    print_line("- MQTT TIMER INTERRUPT -", debug=True)
    _thread.start_new_thread(publish_alive_status, ())
    start_alive_timer()


def start_alive_timer():
    global mqtt_alive_timer
    mqtt_alive_timer.cancel()
    print_line("- stopped MQTT timer", debug=True)
    mqtt_alive_timer = threading.Timer(ALIVE_TIMEOUT_IN_SECONDS, mqtt_alive_handler)
    mqtt_alive_timer.start()
    print_line(
        "- started MQTT timer - every {} seconds".format(ALIVE_TIMEOUT_IN_SECONDS),
        debug=True,
    )


#  MQTT ALIVE TIMER
mqtt_alive_timer = threading.Timer(ALIVE_TIMEOUT_IN_SECONDS, mqtt_alive_handler)


#  ----------------------
#  MQTT Setup and Startup
#
#  MQTT connection
lwt_sensor_topic = "{}/sensor/{}/status".format(base_topic, device_name.lower())
lwt_binary_sensor_topic = "{}/binary_sensor/{}/status".format(
    base_topic, device_name.lower()
)
lwt_command_topic = "{}/command/{}/status".format(base_topic, device_name.lower())
lwt_online_val = "online"
lwt_offline_val = "offline"

# MQTT subscription
command_base_topic = "{}/command/{}".format(base_topic, device_name.lower())


print_line("Connecting to MQTT broker ...", verbose=True)
mqtt_client = mqtt.Client()
#  connect callback functions
mqtt_client.on_connect = on_connect
mqtt_client.on_disconnect = on_disconnect
mqtt_client.on_publish = on_publish
mqtt_client.on_subscribe = on_subscribe
mqtt_client.on_message = on_message

mqtt_client.will_set(lwt_sensor_topic, payload=lwt_offline_val, retain=True)
mqtt_client.will_set(lwt_binary_sensor_topic, payload=lwt_offline_val, retain=True)
mqtt_client.will_set(lwt_command_topic, payload=lwt_offline_val, retain=True)

if config["MQTT"].getboolean("tls", False):
    mqtt_client.tls_set(
        ca_certs=config["MQTT"].get("tls_a_cert", None),
        keyfile=config["MQTT"].get("tls_keyfile", None),
        certfile=config["MQTT"].get("tls_certfile", None),
        tls_version=ssl.PROTOCOL_SSLv23,
    )

mqtt_username = os.environ.get("MQTT_USERNAME", config["MQTT"].get("username"))
mqtt_password = os.environ.get("MQTT_PASSWORD", config["MQTT"].get("password", None))
if mqtt_username:
    mqtt_client.username_pw_set(mqtt_username, mqtt_password)
try:
    mqtt_client.connect(
        os.environ.get("MQTT_HOSTNAME", config["MQTT"].get("hostname", "localhost")),
        port=int(os.environ.get("MQTT_PORT", config["MQTT"].get("port", "1883"))),
        keepalive=config["MQTT"].getint("keepalive", 60),
    )
except ConnectionError:
    print_line(
        'MQTT connection error. Please check your settings in the configuration \
		file "config.ini"',
        error=True,
        sd_notify=True,
    )
    sys.exit(1)
else:
    mqtt_client.publish(lwt_sensor_topic, payload=lwt_online_val, retain=False)
    mqtt_client.publish(lwt_binary_sensor_topic, payload=lwt_online_val, retain=False)
    mqtt_client.publish(lwt_command_topic, payload=lwt_online_val, retain=False)
    mqtt_client.loop_start()

    while not mqtt_client_connected:  #  wait in loop
        print_line(
            "* Wait on mqtt_client_connected=[{}]".format(mqtt_client_connected),
            debug=True,
        )
        sleep(1.0)  #  some slack to establish the connection

    start_alive_timer()

sd_notifier.notify("READY=1")


#  ---------------------------------------
#  perform MQTT discovery announcement ...
#
mac_basic = rpi_mac_address.lower().replace(":", "")
mac_left = mac_basic[:6]
mac_right = mac_basic[6:]
print_line(
    "mac lt=[{}],  rt=[{}], mac=[{}]".format(mac_left, mac_right, mac_basic), debug=True
)
uniqID = "RPi-{}Mon{}".format(mac_left, mac_right)

#  Raspberry Pi (rpi) monitor device with 6 sensors and 1 binary sensor
LD_MONITOR = "monitor"  #  sensor
LD_CPU_TEMP = "temperatur"  #  sensor
LD_DISK_USED = "disk_used"  #  sensor
LD_CPU_USAGE_1M = "cpu_load_1m"  #  sensor
LD_CPU_USAGE_5M = "cpu_load_5m"  #  sensor
LD_MEM_USED = "mem_used"  #  sensor
LD_OS_UPDATE_STATUS = "os_status"  #  binary_sensor
LDS_PAYLOAD_NAME = "info"

#  Verify CPU architecture to select appropriate logo for cpu_usage sensors
if rpi_cpu_model["Architecture"].find("armv") >= 0:
    cpu_icon = "mdi:cpu-32-bit"
else:
    cpu_icon = "mdi:cpu-64-bit"

#  Publish MQTT auto discovery ....
#  table of key items to be published for sensors, binary_sensors and commands:
detectorValues = OrderedDict(
    [
        (
            LD_MONITOR,
            OrderedDict(
                topic_category="sensor",
                title="{} Monitor".format(rpi_hostname),
                device_class="timestamp",
                device_ident="Raspberry Pi {}".format(rpi_hostname.title()),
                icon="mdi:raspberry-pi",
                json_attr="yes",
                json_value="Timestamp",
            ),
        ),
        (
            LD_CPU_TEMP,
            dict(
                title="{} CPU Temp".format(rpi_hostname),
                topic_category="sensor",
                device_class="temperature",
                unit="°C",
                icon="mdi:thermometer",
                json_value="Temp_CPU",
            ),
        ),
        (
            LD_CPU_USAGE_1M,
            dict(
                title="{} CPU Load (1 min)".format(rpi_hostname.title()),
                topic_category="sensor",
                unit="%",
                icon=cpu_icon,
                json_value="CPU_Load_1min",
            ),
        ),
        (
            LD_CPU_USAGE_5M,
            dict(
                title="{} CPU Load (5 min)".format(rpi_hostname.title()),
                topic_category="sensor",
                unit="%",
                icon=cpu_icon,
                json_value="CPU_Load_5min",
            ),
        ),
        (
            LD_MEM_USED,
            dict(
                title="{} Memory Usage".format(rpi_hostname),
                topic_category="sensor",
                unit="%",
                icon="mdi:memory",
                json_value="Memory_Used",
            ),
        ),
        (
            LD_DISK_USED,
            dict(
                title="{} Disk Usage".format(rpi_hostname),
                topic_category="sensor",
                unit="%",
                icon="mdi:sd",
                json_value="Drive_Size_Used",
            ),
        ),
        (
            LD_OS_UPDATE_STATUS,
            dict(
                title="{} Operating System".format(rpi_hostname),
                topic_category="binary_sensor",
                device_class="update",
                icon="mdi:package-up",
                binary="True",
            ),
        ),
    ]
)


for [command, _] in commands.items():
    print_line("- REGISTER command: [{}]".format(command), debug=True)
    icon_name = "mdi:gesture-tap"
    if "reboot" in command:
        icon_name = "mdi:restart"
    elif "shutdown" in command:
        icon_name = "mdi:power"
    elif "service" in command:
        icon_name = "mdi:cog-counterclockwise"
    elif "update" in command:
        icon_name = "mdi:update"
    detectorValues.update(
        {
            command: dict(
                title="{}".format(command.replace("_", " ")),
                object_id="{}_{}_command".format(rpi_hostname, command),
                topic_category="button",
                no_title_prefix="yes",
                icon=icon_name,
                command=command,
                command_topic="{}/{}".format(command_base_topic, command),
            )
        }
    )

print_line(
    "Announcing Raspberry Pi Monitoring device to MQTT broker for auto-discovery ..."
)
print_line("- detectorValues=[{}]".format(detectorValues), debug=True)

values_topic_rel = "{}/{}".format("~", LD_MONITOR)
values_topic = "{}/sensor/{}/{}".format(base_topic, device_name.lower(), LD_MONITOR)
activity_topic_rel = "{}/status".format("~")
binary_base_topic = "{}/binary_sensor/{}".format(base_topic, device_name.lower())
binary_state = "{}/state".format(binary_base_topic)
binary_attributes = "{}/attributes".format(binary_base_topic)


# command_topic_rel = '~/set'

#  auto-discovery of sensors, binary sensors, commands
for [sensor, params] in detectorValues.items():
    discovery_topic = "{}/{}/{}/{}/config".format(
        discovery_prefix, params["topic_category"], device_name.lower(), sensor
    )
    sensor_base_topic = "{}/{}/{}".format(
        base_topic, params["topic_category"], device_name.lower()
    )

    payload = OrderedDict()
    payload["name"] = "{}".format(params["title"].title())
    payload["uniq_id"] = "{}_{}".format(uniqID, sensor.lower())

    if "device_class" in params:
        payload["dev_cla"] = params["device_class"]

    if "unit" in params:
        payload["unit_of_measurement"] = params["unit"]

    if "json_value" in params:
        payload["stat_t"] = values_topic_rel
        payload["val_tpl"] = "{{{{ value_json.{}.{} }}}}".format(
            LDS_PAYLOAD_NAME, params["json_value"]
        )

    if "object_id" in params:
        payload["obj_id"] = params["object_id"]

    if "command" in params:
        payload["~"] = command_base_topic
        payload["cmd_t"] = "~/{}".format(params["command"])
        payload["json_attr_t"] = "~/{}/attributes".format(params["command"])
    else:
        payload["~"] = sensor_base_topic

    if "binary" in params:
        payload["stat_t"] = "~/state"
        payload["pl_on"] = "on"
        payload["pl_off"] = "off"
        payload["json_attr_t"] = "~/attributes"

    payload["avty_t"] = activity_topic_rel
    payload["pl_avail"] = lwt_online_val
    payload["pl_not_avail"] = lwt_offline_val
    # if 'trigger_type' in params:
    # 	payload['type'] = params['trigger_type']
    # if 'trigger_subtype' in params:
    # 	payload['subtype'] = params['trigger_subtype']

    if "icon" in params:
        payload["ic"] = params["icon"]

    if "json_attr" in params:
        payload["json_attr_t"] = values_topic_rel
        payload["json_attr_tpl"] = "{{{{ value_json.{} | tojson }}}}".format(
            LDS_PAYLOAD_NAME
        )

    if "device_ident" in params:
        payload["dev"] = {
            "identifiers": ["{}".format(uniqID)],
            "manufacturer": "Raspbery Pi (Trading) Ltd.",
            "name": params["device_ident"],
            "model": "{}".format(rpi_model),
            "sw_version": "{} {}".format(rpi_os_release, rpi_os_version),
        }
    else:
        payload["dev"] = {
            "identifiers": ["{}".format(uniqID)],
        }

    mqtt_client.publish(discovery_topic, json.dumps(payload), 1, retain=True)


#  -------------------------------------------------------
#  timer and timer functionss for handling reporting cycle
#
def reporting_handler():
    print_line("- REPORTING TIMER INTERRUPT -", debug=True)
    handle_interrupt(TIMER_INTERRUPT)  #  '0' means we have a timer interrupt!
    start_reporting_timer()


def start_reporting_timer():
    global reporting_timer
    reporting_timer.cancel()
    reporting_timer = threading.Timer(
        reporting_interval_in_minutes * 60.0, reporting_handler
    )
    reporting_timer.start()
    print_line(
        "- started reporting cycle timer - every {} seconds".format(
            reporting_interval_in_minutes * 60.0
        ),
        debug=True,
    )


#  TIMER
reporting_timer = threading.Timer(
    reporting_interval_in_minutes * 60.0, reporting_handler
)
#  BOOL tracking state of TIMER
reported_first_time = False


#  -----------------------------
#  MQTT transmit helper routines
#
SCRIPT_TIMESTAMP = "Timestamp"
RPI_MODEL = "Raspberry_Model"
RPI_HOSTNAME = "Hostname"
RPI_FQDN = "FQDN"
RPI_OS_PENDING_UPDATES = "OS_Pending_Updates"
RPI_OS_RELEASE = "OS_Release"
RPI_OS_VERSION = "OS_Version"
RPI_UPTIME = "Up_Time"
RPI_OS_LAST_UPGRADE = "OS_Last_Upgrade"
RPI_DRIVE_INSTALLED = "Drive_Size_Installed"
RPI_DRIVE_USED = "Drive_Size_Used"
RPI_DRIVE_MOUNTED = "Drive(s)_Mounted"
RPI_MEMORY_INSTALLED = "Memory_Installed"
RPI_MEMORY_USED = "Memory_Used"
RPI_CPU_TEMP = "Temp_CPU"
RPI_CPU_LOAD_1M = "CPU_Load_1min"
RPI_CPU_LOAD_5M = "CPU_Load_5min"
RPI_GPU_TEMP = "Temp_GPU"
RPI_SCRIPT = "Reporter"
RPI_NETWORK = "Network_Interface(s)"
RPI_PENDING_UPDATES_MODULES = "Pending Updates"
RPI_CPU = "CPU"
SCRIPT_REPORT_INTERVAL = "Reporter_Interval"


def sendStatus(timestamp, nothing):
    """ """
    global rpi_security_status
    rpiData = OrderedDict()
    rpiData[SCRIPT_TIMESTAMP] = (
        timestamp.astimezone().replace(microsecond=0).isoformat()
    )
    rpiData[RPI_MODEL] = rpi_model
    rpiData[RPI_HOSTNAME] = rpi_hostname
    rpiData[RPI_FQDN] = rpi_fqdn
    rpiData[RPI_OS_RELEASE] = rpi_os_release
    rpiData[RPI_OS_VERSION] = rpi_os_version
    rpiData[RPI_OS_PENDING_UPDATES] = rpi_os_nbr_of_pending_updates
    rpiData[RPI_OS_LAST_UPGRADE] = rpi_timestamp_of_last_os_upgrade
    rpiData[RPI_UPTIME] = rpi_uptime
    rpiData[RPI_DRIVE_INSTALLED] = "{} {}".format(rpi_drive_size, rpi_drive_size_unit)
    rpiData[RPI_DRIVE_USED] = rpi_drive_used
    rpiData[RPI_MEMORY_INSTALLED] = "{} {}".format(
        rpi_memory_installed, rpi_memory_installed_unit
    )
    rpiData[RPI_MEMORY_USED] = rpi_memory_used
    rpiData[RPI_CPU_TEMP] = rpi_cpu_temp
    rpiData[RPI_GPU_TEMP] = rpi_gpu_temp
    rpiData[RPI_CPU_LOAD_1M] = rpi_cpu_load_1m
    rpiData[RPI_CPU_LOAD_5M] = rpi_cpu_load_5m
    rpiData[RPI_SCRIPT] = rpi_mqtt_script
    rpiData[SCRIPT_REPORT_INTERVAL] = "{} min".format(reporting_interval_in_minutes)
    rpiData[RPI_CPU] = rpi_cpu_model
    rpiData[RPI_DRIVE_MOUNTED] = rpi_drive_mounted
    rpiData[RPI_NETWORK] = rpi_network_interfaces

    rpiTopDict = OrderedDict()
    rpiTopDict[LDS_PAYLOAD_NAME] = rpiData

    _thread.start_new_thread(publish_monitor_data, (rpiTopDict, values_topic))


def publish_monitor_data(latestData, topic):
    """"""
    print_line(
        'Publishing to MQTT topic  "{}, Data:{}"'.format(topic, json.dumps(latestData))
    )
    mqtt_client.publish("{}".format(topic), json.dumps(latestData), 1, retain=False)
    sleep(0.5)


def publish_binary_state(status, topic):
    print_line('Publishing to MQTT topic "{}, Data:{}"'.format(topic, status))
    mqtt_client.publish("{}".format(topic), payload="{}".format(status), retain=False)
    sleep(0.5)


def update_dynamic_values():
    global rpi_uptime, rpi_cpu_temp, rpi_gpu_temp
    global rpi_memory_used, rpi_drive_used
    global rpi_cpu_load_1m, rpi_cpu_load_5m, rpi_cpu_load_15m
    rpi_uptime = rpi.get_uptime()
    print_line("rpi_uptime = [{}]".format(rpi_uptime), debug=True)
    rpi_cpu_temp, rpi_gpu_temp = rpi.get_device_temperatures()
    print_line("rpi_cpu_temp = [{}]".format(rpi_cpu_temp), debug=True)
    print_line("rpi_gpu_temp = [{}]".format(rpi_gpu_temp), debug=True)
    rpi_memory_used = rpi.get_device_memory_used()
    print_line("rpi_memory_used = [{}%]".format(rpi_memory_used), debug=True)
    rpi_drive_used = rpi.get_device_drive_used()
    print_line("rpi_drive_used = [{}%]".format(rpi_drive_used), debug=True)
    rpi_cpu_load_1m, rpi_cpu_load_5m, rpi_cpu_load_15m = rpi.get_cpu_load()
    print_line(
        "rpi_cpu_loads 1m|5m|15m = [{}|{}|{}]".format(
            rpi_cpu_load_1m, rpi_cpu_load_5m, rpi_cpu_load_15m
        ),
        debug=True,
    )


def handle_interrupt(channel):
    global reported_first_time
    sourceID = "<< INTR(" + str(channel) + ")"
    current_timestamp = datetime.now(local_tz)
    print_line(
        sourceID
        + " >> Time to report! {}".format(
            current_timestamp.strftime("%H:%M:%S - %Y/%m/%d")
        ),
        verbose=True,
    )
    update_dynamic_values()
    if not opt_stall or not reported_first_time and opt_stall:
        #  report our new detection to MQTT
        _thread.start_new_thread(sendStatus, (current_timestamp, ""))
        reported_first_time = True
    else:
        print_line(
            sourceID
            + " >> Time to report! {} but SKIPPED (Test: stall)".format(
                current_timestamp.strftime("%H:%M:%S - %Y/%m/%d")
            ),
            verbose=True,
        )


print_line("* afterMQTTConnect()", verbose=True)
#  start reporting timer
start_reporting_timer()
#  do first report
print_line("* first reporting!", debug=True, verbose=True)
(
    rpi_os_nbr_of_pending_updates,
    rpi_os_pending_updates_content,
) = rpi.get_os_pending_updates()
handle_interrupt(0)


#  ------------------------------------------------------------
#  now just hang in forever, until script is stopped externally
#
try:
    while True:
        #  the reporting timer does the work
        #  ... sleep until next check for pending updates ...
        sleep(timespan_update_check_in_seconds)

        #  check for pending updates
        print_line("* check for pending updates ...", console=True, sd_notify=True)
        (
            rpi_os_nbr_of_pending_updates,
            rpi_os_pending_updates_content,
        ) = rpi.get_os_pending_updates()

        if rpi_os_nbr_of_pending_updates > 0:
            _thread.start_new_thread(publish_binary_state, ("on", binary_state))
        else:
            _thread.start_new_thread(publish_binary_state, ("off", binary_state))

        _thread.start_new_thread(
            publish_monitor_data, (rpi_os_pending_updates_content, binary_attributes)
        )

finally:
    #  publish shutdown message to mqtt broker
    publish_shutting_down_status()
    #  cleanup timers
    reporting_timer.cancel()
    mqtt_alive_timer.cancel()
    mqtt_client.disconnect()
    print_line("* MQTT Disconnect()", verbose=True, debug=True)
