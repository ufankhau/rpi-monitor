#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
#  ------------------------------------------------------------------------------
#  Program to monitor the following parameters of a Raspberry Pi and send the 
#  the data via MQTT to Home Assistant
#  - General data of the Raspberry Pi, like
#	   - model (e.g. RPI 3B+, RPI 4B, RPI ZeroW)
#    - running operating system (release and version)
#    - network interfaces with MAC and IP addresses
#    - hostname, fqdn
#    - number of CPUs
#    - architecture
#    - mounted filesystem(s)
#  - Operating data of the Raspberry Pi, like
#    - date of last update and upgrade of OS
#    - uptime
#    - Temperature CPU
#    - Temperature GPU (only, if command vcgencmd is available on the Raspberry Pi)
#    - % of RAM used
#    - % of used disk space
#    - CPU load (1m and 5m)
#    - system security status ("safe" if OS update less than 1 day old (default), 
#      and upgrade < 7 days, otherwise "unsafe", thresholds can be set in "config.ini" file,
#      ranges are hardcoded)
#
#  --------------------------
#  import necessary libraries
#  --------------------------
import _thread
from datetime import datetime, timedelta
from tzlocal import get_localzone
#import subprocess
import sys
import ssl
import json
import os
import os.path
import argparse
import threading
#from time import time, sleep, localtime, strftime
from time import sleep, localtime, strftime
from collections import OrderedDict
#from colorama import init as colorama_init
from colorama import Fore, Style
from configparser import ConfigParser
from unidecode import unidecode 
import paho.mqtt.client as mqtt
import sdnotify

# import rpi module located in subdirectory /rpi
#sys.path.append('rpi')
import rpi

script_version = "1.7.1"
script_name = 'rpi-monitor.py'
script_info = '{} v{}'.format(script_name, script_version)
project_name = 'rpi-monitor'
project_url = 'https://github.com/ufankhau/rpi-monitor'

local_tz = get_localzone()


#  ----------------
#  Helper Functions
#
#  Logging
def print_line(text, error=False, warning=False, info=False, verbose=False, debug=False,
				console=True, sd_notify=False):
	timestamp = strftime('%Y-%m-%d %H:%M:%S', localtime())
	if console:
		if error:
			print("{}{}[{}] {}{}{}".format(Fore.RED, Style.BRIGHT, timestamp, Style.RESET_ALL,
							text, Style.RESET_ALL), file=sys.stderr)
		elif warning:
			print("{}[{}] {}{}{}".format(Fore.YELLOW, timestamp, Style.RESET_ALL, text, 
							Style.RESET_ALL))
		elif info or verbose:
			if opt_verbose:
				print("{}[{}] {}{}{}{}".format(Fore.GREEN, timestamp, Fore.YELLOW, text, Style.RESET_ALL))
		elif debug:
			if opt_debug:
				print("{}{} - (DBG): {}{}".format(Fore.CYAN, timestamp, text, Style.RESET_ALL))
		else:
			print("{}[{}] {}{}{}".format(Fore.GREEN, timestamp, Style.RESET_ALL, text, Style.RESET_ALL))
	timestamp_sd = strftime('%b %d %H:%M:%S', localtime())
	if sd_notify:
		sd_notifier.notify('STATUS={} - {}.'.format(timestamp_sd, unidecode(text)))

#  check, that python 3 is available
if False:
	# will be caught by python 2.7 to be illegal syntax
	print_line('Sorry, this script requires a python 3 runtime environment.', file=sys.stderr)
	os._exit(1)

#  Initiate variables
opt_debug = False
opt_verbose = False

#  Systemd Service Notifications - https://github.com/bb4242/sdnotify
sd_notifier = sdnotify.SystemdNotifier()


#  --------
#  argparse
# 
arg = argparse.ArgumentParser(description=project_name, epilog='For further details see: ' +\
	project_url)
arg.add_argument("-v", "--verbose", help="increase output verbosity", action="store_true")
arg.add_argument("-d", "--debug", help="show debug output", action="store_true")
arg.add_argument("-s", "--stall", help="TEST: report only the first time", action="store_true")
arg.add_argument("-c", '--config_dir', help='set directory where config.ini is located', \
	default=sys.path[0])
parse_args = arg.parse_args()

config_dir = parse_args.config_dir
opt_debug = parse_args.debug
opt_verbose = parse_args.verbose
opt_stall = parse_args.stall

print_line(script_info, info=True)
if opt_verbose:
	print_line('Verbose enabled', info=True)
if opt_debug:
	print_line('Debug enabled', debug=True)
if opt_stall:
	print_line('Test: Stall (no-re-reporting) enabled', debug=True)


#  -------------
#  MQTT Handlers
# 
mqtt_client_connected = False
print_line('* init mqtt_client_connected=[{}]'.format(mqtt_client_connected), debug=True)
mqtt_client_should_attempt_reconnect = True

def on_connect(client, userdata, flags, rc):
	global mqtt_client_connected
	if rc == 0:
		print_line('* MQTT connection established', console=True, sd_notify=True)
		print_line('')  #  blank line
		mqtt_client_connected = True
		print_line('on_connect() mqtt_client_connected=[{}]'.format(mqtt_client_connected), debug=True)
	else:
		print_line('! Connection error with result code {} - {}'.format(str(rc), \
			mqtt.connack_string(rc)), error=True)
		print_line('MQTT Connection error with result code {} - {}'.format(str(rc), \
			mqtt.connack_string(rc)), error=True, sd_notify=True)
		mqtt_client_connected = False  #  technically NOT useful but readying possible new shape ...
		print_line('on_connected() mqtt_client_connected=[{}]'.format(mqtt_client_connected), \
			debug=True, error=True)
		# kill main thread
		os._exit(1)

def on_publish(client, userdata, mid):
	print_line('* Data successfully published.', debug=True)
	pass


#  -----------------------
#  Load Configuration File
# 
config = ConfigParser(delimiters=('=', ), inline_comment_prefixes=('#'))
config.optionxform = str
try:
	with open(os.path.join(config_dir, 'config.ini')) as config_file:
		config.read_file(config_file)
except IOError:
	print_line('No configuration file "config.ini"', error=True, sd_notify=True)
	sys.exit(1)

daemon_enabled = config['Daemon'].getboolean('enabled', True)

default_base_topic = 'home/nodes'
base_topic = config['MQTT'].get('base_topic', default_base_topic).lower()

default_sensor_name = 'rpi'
sensor_name = config['MQTT'].get('sensor_name', default_sensor_name).lower()

#  by default Home Assistant listens to /homeassistant
default_discovery_prefix = 'homeassistant'
discovery_prefix = config['MQTT'].get('discovery_prefix', default_discovery_prefix).lower()

#  reporting interval of Raspberry values in minutes [1 - 20]
min_interval_in_minutes = 1
max_interval_in_minutes = 20
default_interval_in_minutes = 5
interval_in_minutes = config['Daemon'].getint('interval_in_minutes', default_interval_in_minutes)

#  default domain when hostname -f doesn't return it
default_domain = ''
fallback_domain = config['Daemon'].get('fallback_domain', default_domain).lower()

#  the apt update command should be run daily. Hence the default is set to 3
min_update_days = 1
max_update_days = 7
default_update_days = 3
OS_update_days = config['Daemon'].getint('OS_update_days', default_update_days)
max_time_since_update = OS_update_days*24*60*60

#  maximum time since last upgrade of OS to consider heahlth of Raspberry OS as "Safe"
min_upgrade_days = 1
max_upgrade_days = 14
default_upgrade_days = 7
OS_upgrade_days = config['Daemon'].getint('OS_upgrade_days', default_upgrade_days)
max_time_since_upgrade = OS_upgrade_days*24*60*60

#  check configuration
if (OS_update_days < min_update_days) or (OS_update_days > max_update_days):
	print_line('ERROR: invalid "OS_update_days" found in configuration file: '+\
		'"config.ini"! Must be within range [{}-{}]. Fix and try again .... aborting'\
		.format(min_update_days, max_update_days), error=True, sd_notify=True)
	sys.exit(1)
if (OS_upgrade_days < min_upgrade_days) or (OS_upgrade_days > max_upgrade_days):
	print_line('ERROR: invalid "OS_upgrade_days" found in configuration file: '+\
		'"config.ini"! Must be within range [{}-{}]. Fix and try again .... aborting'\
		.format(min_upgrade_days, max_upgrade_days), error=True, sd_notify=True)
	sys.exit(1)
if (interval_in_minutes < min_interval_in_minutes) or (interval_in_minutes > max_interval_in_minutes):
	print_line('ERROR: invalid "interval_in_minutes" found in configuration file: '+\
		'"config.ini"! Must be [{}-{}] Fix and try again .... aborting'.format(\
		min_interval_in_minutes, max_interval_in_minutes), error=True, sd_notify=True)
	sys.exit(1)

#  ensure requried values whtin sections of our config file are present
if not config['MQTT']:
	print_line('ERROR: No MQTT settings found in configuration file "config.ini"! \
		Fix and try again ... aborting', error=True, sd_notify=True)
	sys.exit(1)

print_line('Configuration accepted', debug=True, sd_notify=True)


#  -----------------
#  List of Constants
ALIVE_TIMEOUT_IN_SECONDS = 60
TIMER_INTERRUPT = (-1)
TEST_INTERRUPT = (-2)

mem_units = {
	0: 'kB',
	1: 'MB',
	2: 'GB',
	3: 'TB'
}


#  --------------------------------
#  Raspberry Pi Variables Monitored
#  
#  ... with static content
rpi_cpu_model = OrderedDict()
rpi_fqdn = ''
rpi_fs_mounted = OrderedDict()
rpi_fs_size = 0
rpi_fs_size_unit = ''
rpi_hostname = ''
rpi_mac_address = ''
rpi_model = ''
rpi_mqtt_script = script_info.replace('.py', '')
rpi_network_interfaces = OrderedDict()
rpi_number_of_cpu_cores = 0
rpi_os_bit_length = 0
rpi_os_release = ''
rpi_os_version = ''
rpi_ram_installed = 0
rpi_ram_installed_unit = ''

#  ... with dynamic content
rpi_cpu_load_1m = 0.0
rpi_cpu_load_5m = 0.0
rpi_cpu_load_15m = 0.0
rpi_cpu_temp = 0.0
rpi_fs_used = ''
rpi_gpu_temp = 0.0
rpi_ram_used = ''
rpi_security = [
	['OS Update Status', 'safe'],
	['OS Upgrade Status', 'safe']
]
rpi_security_status = 'off'
rpi_time_since_last_os_update = 0
rpi_time_since_last_os_upgrade = 0
rpi_uptime = ''


#  ----------------
#  Load Static Data
rpi_hostname, rpi_fqdn = rpi.get_hostname()
if sensor_name == default_sensor_name:
	sensor_name = 'rpi-{}'.format(rpi_hostname)
print_line('rpi_hostname = [{}]'.format(rpi_hostname), debug=True)
print_line('rpi_fqdn = [{}]'.format(rpi_fqdn), debug=True)
rpi_model = rpi.get_device_model()
print_line('rpi_model = [{}]'.format(rpi_model), debug=True)
rpi_cpu_model = rpi.get_device_cpu_info()
print_line('rpi_cpu_mode = [{}]'.format(rpi_cpu_model), debug=True)
rpi_number_of_cpu_cores = rpi_cpu_model["Core(s)"]
print_line('rpi_nbrCores = [{}]'.format(rpi_number_of_cpu_cores), debug=True)
rpi_os_bit_length = rpi.get_os_bit_length()
print_line('rpi_os_bit_length = [{}]'.format(rpi_os_bit_length), debug=True)
rpi_os_release = "{} - {}-bit".format(rpi.get_os_release(), rpi_os_bit_length)
print_line('rpi_os_release = [{}]'.format(rpi_os_release), debug=True)
rpi_os_version = rpi.get_os_version()
print_line('rpi_os_version = [{}]'.format(rpi_os_version), debug=True)
rpi_ram_installed, unit = rpi.get_device_ram_installed()
rpi_ram_installed_unit = mem_units[unit]
print_line('rpi_mem_installed = [{}{}]'.format(rpi_ram_installed, rpi_ram_installed_unit), debug=True)
rpi_fs_size, unit = rpi.get_filesystem_size()
rpi_fs_size_unit = mem_units[unit]
print_line('rpi_fs_size = [{}{}]'.format(rpi_fs_size, rpi_fs_size_unit), debug=True)
fs_mounted = rpi.get_filesystems_mounted()
print_line('fs_mounted = [{}]'.format(fs_mounted), debug=True)
for line in fs_mounted:
	if line != 'none':
		line_parts = line.split(',')
		rpi_fs_mounted[line_parts[0]] = "-\> {}".format(line_parts[1])
	else:
		mnt_pt = None
		rpi_fs_mounted['none'] = mnt_pt
print_line('rpi_fs_mounted = [{}]'.format(rpi_fs_mounted), debug=True)
rpi_network_interfaces, rpi_mac_address = rpi.get_network_interfaces()
print_line('rpi_interfaces = [{}]'.format(rpi_network_interfaces), debug=True)
print_line('rpi_mac_address = [{}]'.format(rpi_mac_address), debug=True)


#  -----------------------------------------------------
#  timer and timer funcs for ALIVE MQTT notices handling
#  -----------------------------------------------------


def publishAliveStatus():
	print_line('- SEND: yes, still alive - ', debug=True)
	mqtt_client.publish(lwt_topic, payload=lwt_online_val, retain=False)


def aliveTimeoutHandler():
	print_line('- MQTT TIMER INTERRUPT -', debug=True)
	_thread.start_new_thread(publishAliveStatus, ())
	startAliveTimer()


def startAliveTimer():
	global aliveTimeout
	global aliveTimerRunningStatus
	stopAliveTimer()
	aliveTimer = threading.Timer(ALIVE_TIMEOUT_IN_SECONDS, aliveTimeoutHandler)
	aliveTimer.start()
	aliveTimerRunningStatus = True
	print_line('- started MQTT timer - every {} seconds'.format(ALIVE_TIMEOUT_IN_SECONDS), debug=True)


def stopAliveTimer():
	global aliveTimer
	global aliveTimerRunningStatus
	aliveTimer.cancel()
	aliveTimerRunningStatus = False
	print_line('- stopped MQTT timer', debug=True)


def isAliveTimerRunning():
	global aliveTimerRunningStatus
	return aliveTimerRunningStatus

#  our ALIVE TIMER
aliveTimer = threading.Timer(ALIVE_TIMEOUT_IN_SECONDS, aliveTimeoutHandler)
#  our BOOL tracking state of ALIVE TIMER
aliveTimerRunningStatus = False


#  ----------------------
#  MQTT Setup and Startup
#
#  MQTT connection
lwt_topic = '{}/sensor/{}/status'.format(base_topic, sensor_name.lower())
lwt_online_val = 'online'
lwt_offline_val = 'offline'

print_line('Connecting to MQTT broker ...', verbose=True)
mqtt_client = mqtt.Client()
mqtt_client.on_connect = on_connect
mqtt_client.on_publish = on_publish

mqtt_client.will_set(lwt_topic, payload=lwt_offline_val, retain=True)

if config['MQTT'].getboolean('tls', False):
	mqtt_client.tls_set(
		ca_certs=config['MQTT'].get('tls_a_cert', None),
		keyfile=config['MQTT'].get('tls_keyfile', None),
		certfile=config['MQTT'].get('tls_certfile', None),
		tls_version=ssl.PROTOCOL_SSLv23
		)

mqtt_username = os.environ.get("MQTT_USERNAME", config['MQTT'].get('username'))
mqtt_password = os.environ.get("MQTT_PASSWORD", config['MQTT'].get('password', None))
if mqtt_username:
	mqtt_client.username_pw_set(mqtt_username, mqtt_password)
try:
	mqtt_client.connect(os.environ.get('MQTT_HOSTNAME', config['MQTT'].get('hostname', 'localhost')),
		port=int(os.environ.get('MQTT_PORT', config['MQTT'].get('port', '1883'))),
		keepalive=config['MQTT'].getint('keepalive', 60))
except:
	print_line('MQTT connection error. Please check your settings in the configuration \
		file "config.ini"', error=True, sd_notify=True)
	sys.exit(1)
else:
	mqtt_client.publish(lwt_topic, payload=lwt_online_val, retain=False)
	mqtt_client.loop_start()

	while mqtt_client_connected == False:     #  wait in loop
		print_line('* Wait on mqtt_client_connected=[{}]'.format(mqtt_client_connected), debug=True)
		sleep(1.0)      #  some slack to establish the connection

	startAliveTimer()

sd_notifier.notify('READY=1')


#  ---------------------------------------
#  perform MQTT discovery announcement ...
#  ---------------------------------------

#  what RPi device are we on?
#  get hostnames so we can setup MQTT
#getNetworkIFsUsingIP()         #  this will fill-in rpi_mac

mac_basic = rpi_mac_address.lower().replace(":", "")
mac_left = mac_basic[:6]
mac_right = mac_basic[6:]
print_line('mac lt=[{}],  rt=[{}], mac=[{}]'.format(mac_left, mac_right, mac_basic), debug=True)
uniqID = "RPi-{}Mon{}".format(mac_left, mac_right)

#  Raspberry Pi (rpi) monitor device with 6 sensors and 1 binary sensor
LD_MONITOR = "monitor"    		            #  sensor
LD_CPU_TEMP = "temp_cpu_c"    		        #  sensor
LD_FS_USED = "disk_used"    		          #  sensor
LD_CPU_USAGE_1M = "cpu_load_1m"           #  sensor 
LD_CPU_USAGE_5M = "cpu_load_5m"	          #  sensor
LD_MEM_USED = "ram_used_prcnt"		        #  sensor
LD_SECURITY_STATUS = "os_security_status"	#  binary_sensor
LDS_PAYLOAD_NAME = "info"

#  Verify CPU architecture to select appropriate logo for cpu_usage sensors
if rpi_cpu_model["Architecture"].find('armv') >= 0:
	cpu_icon = "mdi:cpu-32-bit"
else:
	cpu_icon = "mdi:cpu-64-bit"

#  Publish MQTT auto discovery ....
#  table of key items to be published for sensors:
detectorValues = OrderedDict([
	(LD_MONITOR, dict(
		title="{} RPi Monitor".format(rpi_hostname),
		topic_category="sensor",
		device_class="timestamp",
		device_ident='Raspberry Pi {}'.format(rpi_hostname.title()),
		no_title_prefix="yes",
		icon='mdi:raspberry-pi',
		json_attr="yes",
		json_value="Timestamp", 
	)),		
	(LD_CPU_TEMP, dict(
		title="{} CPU Temp".format(rpi_hostname), 
		topic_category="sensor",
		device_class="temperature",
		no_title_prefix="yes",
		unit="°C",
		icon='mdi:thermometer', 
		json_value="Temp_CPU_c", 
	)),
	(LD_CPU_USAGE_1M, dict(
		title="{} CPU Load (1 min)".format(rpi_hostname.title()),
		topic_category="sensor",
		no_title_prefix="yes",
		unit="%",
		icon=cpu_icon,
		json_value="CPU_Load_1_min",  
	)),
	(LD_CPU_USAGE_5M, dict(
		title="{} CPU Load (5 min)".format(rpi_hostname.title()),
		topic_category="sensor",
		no_title_prefix="yes",
		unit="%",
		icon=cpu_icon,
		json_value="CPU_Load_5_min",  
	)),
	(LD_MEM_USED, dict(
		title="{} Memory Usage".format(rpi_hostname),
		topic_category="sensor",
		no_title_prefix="yes",
		unit="%",
		icon='mdi:memory',
		json_value="RAM_used_prcnt",  
	)),
	(LD_FS_USED, dict(
		title="{} Disk Usage".format(rpi_hostname), 
		topic_category="sensor",
		no_title_prefix="yes",
		unit="%",
		icon='mdi:sd',
		json_value="FS_used_prcnt",
	)),
])

print_line('Announcing Raspberry Pi Monitoring device to MQTT broker for auto-discovery ...')

base_topic = '{}/sensor/{}'.format(base_topic, sensor_name.lower())
values_topic_rel = '{}/{}'.format('~', LD_MONITOR)
values_topic = '{}/{}'.format(base_topic, LD_MONITOR)
activity_topic_rel = '{}/status'.format('~')
activity_topic = '{}/status'.format(base_topic)

#  auto-discovery of sensors
for [sensor, params] in detectorValues.items():
	discovery_topic = '{}/sensor/{}/{}/config'.format(discovery_prefix, sensor_name.lower(), \
		sensor)
	payload = OrderedDict()
	if 'no_title_prefix' in params:
		payload['name'] = "{}".format(params['title'].title())
	else:
		payload['name'] = "{} {}".format(sensor_name.title(), params['title'].title())
	payload['uniq_id'] = "{}_{}".format(uniqID, sensor.lower())
	if 'device_class' in params:
		payload['dev_cla'] = params['device_class']
	if 'unit' in params:
		payload['unit_of_measurement'] = params['unit']
	if 'json_value' in params:
		payload['stat_t'] = values_topic_rel
		payload['val_tpl'] = "{{{{ value_json.{}.{} }}}}".format(LDS_PAYLOAD_NAME, \
			params['json_value'])
	payload['~'] = base_topic
	payload['pl_avail'] = lwt_online_val
	payload['pl_not_avail'] = lwt_offline_val
	if 'icon' in params:
		payload['ic'] = params['icon']
	payload['avty_t'] = activity_topic_rel
	if 'json_attr' in params:
		payload['json_attr_t'] = values_topic_rel
		payload['json_attr_tpl'] = '{{{{ value_json.{} | tojson }}}}'.format(LDS_PAYLOAD_NAME)
	if 'device_ident' in params:
		payload['dev'] = {
			'identifiers' : ["{}".format(uniqID)],
			'manufacturer' : 'Raspbery Pi (Trading) Ltd.',
			'name' : params['device_ident'],
			'model' : '{}'.format(rpi_model),
			'sw_version' : "{} {}".format(rpi_os_release, rpi_os_version)
		}
	else:
		payload['dev'] = {
			'identifiers' : ["{}".format(uniqID)],
		}

	mqtt_client.publish(discovery_topic, json.dumps(payload), 1, retain=True)

#  auto-discovery of binary_sensor
discovery_topic = '{}/binary_sensor/{}/config'.format(discovery_prefix, sensor_name.lower())
payload = OrderedDict()
payload['name'] = "{} Security Status".format(rpi_hostname.title())
payload['uniq_id'] = "{}_{}".format(uniqID, LD_SECURITY_STATUS)
payload['dev_cla'] = "safety"
payload['payload_on'] = "on"
payload['payload_off'] = "off"
payload['state_topic'] = "home/nodes/binary_sensor/{}/status".format(sensor_name.lower())
payload['json_attr_t'] = "home/nodes/binary_sensor/{}".format(sensor_name.lower())
payload['dev'] = {
	'identifiers' : ["{}".format(uniqID)]
}
mqtt_client.publish(discovery_topic, json.dumps(payload), 1, retain=True)


#  -----------------------------------------
#  timer and timer funcs for period handling
#  -----------------------------------------



def periodTimeoutHandler():
	print_line('- PERIOD TIMER INTERRUPT -', debug=True)
	handle_interrupt(TIMER_INTERRUPT)     #  '0' means we have a timer interrupt!
	startPeriodTimer()


def startPeriodTimer():
	global endPeriodTimer
	global periodTimeRunningStatus
	stopPeriodTimer()
	endPeriodTimer = threading.Timer(interval_in_minutes * 60.0, periodTimeoutHandler)
	endPeriodTimer.start()
	periodTimeRunningStatus = True
	print_line('- started PERIOD timer - every {} seconds'.format(interval_in_minutes * 60.0), debug=True)


def stopPeriodTimer():
	global endPeriodTimer
	global periodTimeRunningStatus
	endPeriodTimer.cancel()
	periodTimeRunningStatus = False
	print_line('- stopped PERIOD timer', debug=True)


def isPeriodTimerRunning():
	global periodTimeRunningStatus
	return periodTimeRunningStatus


#  TIMER
endPeriodTimer = threading.Timer(interval_in_minutes * 60.0, periodTimeoutHandler)
#  BOOL tracking state of TIMER
periodTimeRunningStatus = False
reported_first_time = False


#  -----------------------------
#  MQTT transmit helper routines
#  -----------------------------
SCRIPT_TIMESTAMP = "Timestamp"
RPI_MODEL = "Raspberry Model"
RPI_HOSTNAME = "Hostname"
RPI_FQDN = "Fqdn"
RPI_OS_RELEASE = "OS Release"
RPI_OS_VERSION = "OS Version"
RPI_UPTIME = "Up Time"
RPI_OS_LAST_UPDATE = "OS_Last_Update"
RPI_OS_LAST_UPGRADE = "OS_Last_Upgrade"
RPI_FS_SPACE = "FS Total [{}]".format(rpi_fs_size_unit)
RPI_FS_USED = "FS Used [%]"
RPI_FS_MOUNT = "FS Mounted"
RPI_RAM_INSTALLED = "RAM Installed [{}]".format(rpi_ram_installed_unit)
RPI_RAM_USED = "RAM Used [%]"
RPI_CPU_TEMP = "Temp CPU [°C]"
RPI_CPU_LOAD_1M = "CPU Load _1min"
RPI_CPU_LOAD_5M = "CPU Load _5min"
RPI_CPU_LOAD_15M = "CPU Load 15min"
RPI_GPU_TEMP = "Temp GPU [°C]"
RPI_SCRIPT = "Reporter"
RPI_NETWORK = "Network Interfaces"
RPI_OS_UPDATE = rpi_security[0][0]
RPI_OS_UPGRADE = rpi_security[1][0]
RPI_SECURITY_STATUS = "Security Status"
RPI_CPU = "CPU"
# RPI_CPU_MODEL = "Model"
# RPI_CPU_CORES = "Core(s)"
# RPI_CPU_ARCHITECTURE = "Architecture"
# RPI_CPU_SPEED = "Core Speed (min | max)"
# RPI_CPU_SERIAL = "Serial"
SCRIPT_REPORT_INTERVAL = "Reporter Interval [min]"


def sendStatus(timestamp, nothing):
	# prepare and send update of sensor data
	global rpi_security_status
	#global rpi_fs_mount
	rpiData = OrderedDict()
	rpiData[SCRIPT_TIMESTAMP] = timestamp.astimezone().replace(microsecond=0).isoformat()
	rpiData[RPI_MODEL] = rpi_model
	rpiData[RPI_HOSTNAME] = rpi_hostname
	rpiData[RPI_FQDN] = rpi_fqdn
	rpiData[RPI_OS_RELEASE] = rpi_os_release
	rpiData[RPI_OS_VERSION] = rpi_os_version
	rpiData[RPI_OS_LAST_UPDATE] = '{} ago - {}'.format(
																 rpi.format_seconds(rpi_time_since_last_os_update),
																 rpi_security[0][1])
	rpiData[RPI_OS_LAST_UPGRADE] = '{} ago - {}'.format(
																	rpi.format_seconds(rpi_time_since_last_os_upgrade),													rpi_security[1][1])
	rpiData[RPI_UPTIME] = rpi_uptime
	rpiData[RPI_FS_SPACE] = rpi_fs_size
	rpiData[RPI_FS_USED] = '{:.1f}%'.format(rpi_fs_used)
	rpiData[RPI_RAM_INSTALLED] = rpi_ram_installed
	rpiData[RPI_RAM_USED] = '{:.1f}%'.format(rpi_ram_used)
	rpiData[RPI_CPU_TEMP] = '{:.1f}°C'.format(rpi_cpu_temp)
	rpiData[RPI_GPU_TEMP] = '{:.1f}°C'.format(rpi_gpu_temp)
	rpiData[RPI_CPU_LOAD_1M] = rpi_cpu_load_1m
	rpiData[RPI_CPU_LOAD_5M] = rpi_cpu_load_5m
	rpiData[RPI_CPU_LOAD_15M] = rpi_cpu_load_15m

	rpiData[RPI_SCRIPT] = rpi_mqtt_script
	rpiData[SCRIPT_REPORT_INTERVAL] = interval_in_minutes

	rpiData[RPI_CPU] = rpi_cpu_model
	rpiData[RPI_FS_MOUNT] = rpi_fs_mounted
	# if rpi_fs_mounted == 'none':
	# 	rpiData[RPI_FS_MOUNT] = rpi_fs_mounted
	# else:
	# 	rpiData[RPI_FS_MOUNT] = getFSmountDictionary()

	rpiData[RPI_NETWORK] = rpi_network_interfaces
	
	rpiTopDict = OrderedDict()
	rpiTopDict[LDS_PAYLOAD_NAME] = rpiData
	
	_thread.start_new_thread(publishMonitorData, (rpiTopDict, values_topic))

	# prepare and send update for binary_sensor(s)
	rpiSecurity = OrderedDict()
	rpiSecurity[RPI_OS_UPDATE] = rpi_security[0][1]
	rpiSecurity[RPI_OS_UPGRADE] = rpi_security[1][1]
	rpiSecurityTop = OrderedDict()
	rpiSecurityTop[LDS_PAYLOAD_NAME] = rpiSecurity
	topic = "home/nodes/binary_sensor/{}".format(sensor_name.lower())
	_thread.start_new_thread(publishMonitorData, (rpiSecurity, topic))
	rpi_security_status = 'off'
	for i in range(len(rpi_security)):
		if rpi_security[i][1] != 'safe':
			rpi_security_status = 'on'
			topic = "home/nodes/binary_sensor/{}/status".format(sensor_name.lower())
			_thread.start_new_thread(publishSecurityStatus, ('on', topic))
			break
	if rpi_security_status == 'off':
		topic = "home/nodes/binary_sensor/{}/status".format(sensor_name.lower())
		_thread.start_new_thread(publishSecurityStatus, ('off', topic))


# def getCPUDictionary():
# 	#  tuple (modelname, #cores, serial#)
# 	cpuDict = OrderedDict()
# 	#rpi_cpu_tuple = ( cpu_architecture, cpu_model, cpu_cores, cpu_serial )
# 	if rpi_cpu_tuple != '':
# 		cpuDict[RPI_CPU_ARCHITECTURE] = rpi_cpu_tuple[0]
# 		cpuDict[RPI_CPU_MODEL] = rpi_cpu_tuple[1]
# 		cpuDict[RPI_CPU_CORES] = rpi_cpu_tuple[2]
# 		cpuDict[RPI_CPU_SERIAL] = rpi_cpu_tuple[3]
# 	return cpuDict


# def getFSmountDictionary():
# 	fsmountDict = OrderedDict()
# 	for i in range(len(rpi_fs_mount)):
# 		lineParts = rpi_fs_mount[i].split(',')
# 		fsmountDict[lineParts[0]] = '-> '+lineParts[1]
# 	print_line('fsmountDict:{}'.format(fsmountDict), debug=True)
# 	return fsmountDict


# def getNetworkDictionary():
# 	networkDict = OrderedDict()
# 	priorIFKey = ''
# 	tmpData = OrderedDict()
# 	for currTuple in rpi_interfaces:
# 		currIFKey = currTuple[0]
# 		if priorIFKey == '':
# 			priorIFKey = currIFKey
# 		if currIFKey != priorIFKey:
# 			if priorIFKey != '':
# 				networkDict[priorIFKey] = tmpData
# 				tmpData = OrderedDict()
# 				priorIFKey = currIFKey
# 		subKey = currTuple[1]
# 		subValue = currTuple[2]
# 		tmpData[subKey] = subValue
# 	networkDict[priorIFKey] = tmpData
# 	print_line('networkDict:{}'.format(networkDict), debug=True)
# 	return networkDict


def publishMonitorData(latestData, topic):
	print_line('Publishing to MQTT topic  "{}, Data:{}"'.format(topic, json.dumps(latestData)))
	mqtt_client.publish('{}'.format(topic), json.dumps(latestData), 1, retain=False)
	sleep(0.5)


def publishSecurityStatus(status, topic):
	print_line('Publishing to MQTT topic "{}, Data:{}"'.format(topic, status))
	mqtt_client.publish('{}'.format(topic), payload='{}'.format(status), retain=False)
	sleep(0.5)


def update_dynamic_values():
	global rpi_uptime, rpi_cpu_temp, rpi_gpu_temp
	global rpi_time_since_last_os_update,rpi_time_since_last_os_upgrade
	global rpi_ram_used, rpi_fs_used
	rpi_uptime = rpi.get_uptime()
	rpi_cpu_temp, rpi_gpu_temp = rpi.get_device_temperatures()
	rpi_time_since_last_os_update = rpi.get_time_since_last_os_update()
	rpi_time_since_last_os_upgrade = rpi.get_time_since_last_os_upgrade()
	rpi_ram_used = rpi.get_device_ram_used()
	rpi_fs_used = rpi.get_device_ram_used()

#  ---------------------------------------------------------------

def handle_interrupt(channel):
	global reported_first_time
	sourceID = "<< INTR(" + str(channel) + ")"
	current_timestamp = datetime.now(local_tz)
	print_line(sourceID + " >> Time to report! {}".format(current_timestamp.strftime('%H:%M:%S - %Y/%m/%d')), \
		verbose=True)
	update_dynamic_values()
	if (opt_stall == False or reported_first_time == False and opt_stall == True):
		#  report our new detection to MQTT
		_thread.start_new_thread(sendStatus, (current_timestamp, ''))
		reported_first_time = True
	else:
		print_line(sourceID + " >> Time to report! {} but SKIPPED (Test: stall)".format(\
			current_timestamp.strftime('%H:%M:%S - %Y/%m/%d')), verbose=True)


def afterMQTTConnect():
	print_line('* afterMQTTConnect()', verbose=True)
	#  start interval timer
	startPeriodTimer()
	#  do first report
	handle_interrupt(0)


afterMQTTConnect()

#  ------------------------------------------------------------
#  now just hang in forever, until script is stopped externally
#  ------------------------------------------------------------
try:
	while True:
		#  the INTERVAL timer does the work
		sleep(10000)

finally:
	#  cleanup timers
	stopPeriodTimer()
	stopAliveTimer()
