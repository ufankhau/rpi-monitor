#!/usr/bin/python3
#
#  ------------------------------------------------------------------------------
#  Program to monitor the following parameters of a Raspberry Pi
#  and send the data via MQTT to Home Assistant
#  - General data of the Raspberry Pi, like
#	 - model (e.g. RPI 3B+, RPI 4B, RPI ZeroW)
#    - running operating system (e.g. Debian, Raspberry OS, etc.)
#    - kernel version
#    - network interfaces with MAC and IP addresses
#    - hostname, fqdn
#    - number of CPUs
#    - architecture
#  - Operating data of the Raspberry Pi, like
#    - date of last update and upgrade of OS
#    - uptime
#    - Temperature CPU
#    - Temperature GPU (only, if command vcgencmd is available on the Raspberry)
#    - % of RAM used
#    - % of used disk space
#    - CPU load (1m and 5m)
#    - system security status ("safe" if OS update less than 1 day old (default), and upgrade < 7 days, 
#      otherwise "unsafe", thresholds can be set in "config.ini" file)
#
#  --------------------------
#  import necessary libraries
#  --------------------------
import _thread
from datetime import datetime, timedelta
from tzlocal import get_localzone
import subprocess
import sys
import json
import os.path
import argparse
import threading
from time import time, sleep, localtime, strftime
from collections import OrderedDict
from colorama import init as colorama_init
from colorama import Fore, Back, Style
from configparser import ConfigParser
from unidecode import unidecode 
import paho.mqtt.client as mqtt
import sdnotify

script_version = "1.4.3"
script_name = 'rpi-monitor.py'
script_info = '{} v{}'.format(script_name, script_version)
project_name = 'rpi-monitor'
project_url = ''

local_tz = get_localzone()

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

#  -----------------------
#  define logging function
#  -----------------------
def print_line(text, error=False, warning=False, info=False, verbose=False, debug=False, \
	console=True, sd_notify=False):
	timestamp = strftime('%Y-%m-%d %H:%M:%S', localtime())
	if console:
		if error:
			print(Fore.RED + Style.BRIGHT + '[{}] '.format(timestamp) + Style.RESET_ALL + \
				'{}'.format(text) + Style.RESET_ALL, file=sys.stderr)
		elif warning:
			print(Fore.YELLOW + '[{}] ').format(timestamp) + Style.RESET_ALL + \
				'{}'.format(text) + Style.RESET_ALL
		elif info or verbose:
			if opt_verbose:
				print(Fore.GREEN + '[{}] '.format(timestamp) + Fore.YELLOW + '- ' + \
					'{}'.format(text) + Style.RESET_ALL)
		elif debug:
			if opt_debug:
				print(Fore.CYAN + '[{}] '.format(timestamp) + '- (DBG): ' + \
					'{}'.format(text) + Style.RESET_ALL)
		else:
			print(Fore.GREEN + '[{}] '.format(timestamp) + Style.RESET_ALL + \
				'{}'.format(text) + Style.RESET_ALL)
	timestamp_sd = strftime('%b %d %H:%M:%S', localtime())
	if sd_notify:
		sd_notifier.notify('STATUS={} - {}.'.format(timestamp_sd, unidecode(text)))


#  --------
#  argparse
#  --------
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
#  MQTT handlers
#  -------------
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
		print_line('! Connection error with result code {} - {}'.format(str(r), \
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
#  load configuration file
#  -----------------------
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
maxTimesinceUpdate = OS_update_days*24*60*60

#  maximum time since last upgrade of OS to consider heahlth of Raspberry OS as "Safe"
min_upgrade_days = 1
max_upgrade_days = 14
default_upgrade_days = 7
OS_upgrade_days = config['Daemon'].getint('OS_upgrade_days', default_upgrade_days)
maxTimesinceUpgrade = OS_upgrade_days*24*60*60

#  check configuration
#
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


#  --------------------------------
#  Raspberry Pi variables monitored
#  --------------------------------
rpi_mac = ''
rpi_nbrCPUs = ''
rpi_cpu_tuple = []
rpi_model = ''
rpi_model_raw = ''
rpi_hostname = ''
rpi_fqdn = ''
rpi_os = ''
rpi_kernel_version = ''
rpi_fs_used = ''
rpi_fs_space = ''
rpi_fs_mount = []
rpi_mqtt_script = script_info.replace('.py', '')
rpi_interfaces = []
rpi_gpu_temp = ''
rpi_cpu_temp = ''
rpi_ram_usage = ''
rpi_security = [
	['OS Update Status', 'safe'],
	['OS Upgrade Status', 'safe']
	]
rpi_security_status = 'off'


#  --------------
#  getDeviceModel
#  --------------
#  extract info from file /proc/device-tree/model and present it in compacted form
def getDeviceModel():
	global rpi_model
	global rpi_model_raw
	cmdString = "/bin/cat /proc/device-tree/model"
	out = subprocess.Popen(cmdString,
		shell=True,
		stdout=subprocess.PIPE,
		stderr=subprocess.STDOUT)
	stdout,_ = out.communicate()
	rpi_model_raw = stdout.decode('utf-8')
	print_line('rpi_model_raw=[{}]'.format(rpi_model_raw), debug=True)
	#  reduce string length (just more compact, same info)
	rpi_model = rpi_model_raw.replace(' Model ', '').replace(' Plus ', '+').replace('Rev ', ' r').replace('\u0000', '')
	print_line('rpi_model=[{}]'.format(rpi_model), debug=True)


#  ----------
#  getNbrCPUs
#  ----------
#  use command "nproc" to get the number of processors / CPUs
def getNbrCPUs():
	global rpi_nbrCPUs
	cmdString = '/usr/bin/nproc'
	out = subprocess.Popen(cmdString,
		shell=True,
		stdout=subprocess.PIPE,
		stderr=subprocess.STDOUT)
	stdout,_ = out.communicate()
	rpi_nbrCPUs = int(stdout.decode('utf-8').lstrip().rstrip())
	print_line('rpi_getCPUs=[{}]'.format(rpi_nbrCPUs), debug=True)


#  -----------------
#  getDeviceCpuModel
#  -----------------
#  use command "/bin/lscpu | /bin/egrep -i 'model|bogo|vendor'" to extract data on the CPU
#  use command "/bin/cat /sys/firmware/devicetree/base/serial-number" to get the
#  serial number of the Raspberry Pi
def getDeviceCpuModel():
	global rpi_cpu_tuple
	global rpi_nbrCPUs
	cmdString1 = "/usr/bin/lscpu | /bin/egrep -i 'model|bogo|vendor'"
	cmdString2 = "/bin/cat /sys/firmware/devicetree/base/serial-number"
	out = subprocess.Popen(cmdString1,
		shell=True,
		stdout=subprocess.PIPE,
		stderr=subprocess.STDOUT)
	stdout,_ = out.communicate()
	lines = stdout.decode('utf-8').split("\n")
	trimmedLines = []
	for currLine in lines:
		trimmedLine = currLine.lstrip().rstrip()
		trimmedLines.append(trimmedLine)
	cpu_model = ''
	cpu_cores = rpi_nbrCPUs
#	cpu_bogoMIPS = 0
	cpu_serial = ''
	cpu_vendor = ''
	cpu_model_name = ''
	for currLine in trimmedLines:
		lineParts = currLine.split(':')
		currValue = '{?unk?}'
		if len(lineParts) >= 2:
			currValue = lineParts[1].lstrip().rstrip()
		if 'Vendor' in currLine:
			cpu_vendor = currValue.upper()
		if 'Model:' in currLine:
			cpu_model = currValue
		if 'Model name' in currLine:
			cpu_model_name = currValue
#		if 'BogoMIPS' in currLine:
#			cpu_bogoMIPS = '{:.0f}'.format(cpu_cores * float(currValue))
	cpu_model = cpu_vendor + " " + cpu_model_name + " r" + cpu_model
	out = subprocess.Popen(cmdString2,
		shell=True,
		stdout=subprocess.PIPE,
		stderr=subprocess.STDOUT)
	stdout,_ = out.communicate()
	lines = stdout.decode('utf-8').replace('\x00', '').lstrip().rstrip()
	cpu_serial = lines
	rpi_cpu_tuple = ( cpu_model, cpu_cores, cpu_serial )
	print_line('rpi_cpu_tuple=[{}]'.format(rpi_cpu_tuple), debug=True)


#  ---------------------
#  getOSandKernelVersion
#  ---------------------
#  use command "/usr/bin/hostnamectl"
def getOSandKernelVersion():
	global rpi_os
	global rpi_os_version
	cmdString = "/usr/bin/hostnamectl"
	out = subprocess.Popen(cmdString,
		shell=True,
		stdout=subprocess.PIPE,
		stderr=subprocess.STDOUT)
	stdout,_ = out.communicate()
	lines = stdout.decode('utf-8').split("\n")
	trimmedLines = []
	for currLine in lines:
		trimmedLine = currLine.split(":")
		if trimmedLine[0].lstrip() == "Operating System":
			rpi_os = trimmedLine[1].lstrip().rstrip()
		if trimmedLine[0].lstrip() == "Kernel":
			rpi_os_version = trimmedLine[1].lstrip().rstrip()
	print_line('rpi_os=[{}]'.format(rpi_os), debug=True)
	print_line('rpi_os_version=[{}]'.format(rpi_os_version), debug=True)


#  -----------------
#  getDeviceMemUsage
#  -----------------
#  use command "cat /proc/meminfo | grep -i 'mem[TFA]'" to extract info on RAM usage
def getDeviceMemUsage():
	global rpi_ram_used
	cmdString = "/bin/cat /proc/meminfo | grep -i 'mem[TFA]'"
	out = subprocess.Popen(cmdString,
		shell=True,
		stdout=subprocess.PIPE,
		stderr=subprocess.STDOUT)
	stdout,_ = out.communicate()
	lines = stdout.decode('utf-8').split("\n")
	trimmedLines = []
	for currLine in lines:
		trimmedLine = currLine.lstrip().rstrip()
		trimmedLines.append(trimmedLine)
	mem_total = ''
	mem_free = ''
	mem_avail = ''
	for currLine in trimmedLines:
		lineParts = currLine.split()
		if "MemTotal" in currLine:
			mem_total = float(lineParts[1]) / 1024
		if "MemFree" in currLine:
			mem_free = float(lineParts[1]) / 1024
		if "MemAvail" in currLine:
			mem_avail = float(lineParts[1]) / 1024
	rpi_ram_used = '{:.0f}'.format((mem_total - mem_free) / mem_total * 100)
	print_line('rpi_mem_usage=[{}%]'.format(rpi_ram_used), debug=True)


#  ------------------
#  getFileSystemUsage
#  ------------------
#  get disk usage from command "bin/df -m"
def getFileSystemUsage():
	global rpi_fs_space
	global rpi_fs_used
	global rpi_fs_mount
	cmdString = "/bin/df -m | /usr/bin/tail -n +2 | /bin/egrep -v 'tmpfs|boot|overlay|udev'"
	out = subprocess.Popen(cmdString,
		shell=True,
		stdout=subprocess.PIPE,
		stderr=subprocess.STDOUT)
	stdout,_ = out.communicate()
	lines = stdout.decode('utf-8').split("\n")
	trimmedLines = []
	for currLine in lines:
		trimmedLine = currLine.lstrip().rstrip()
		if len(trimmedLine) > 0:
			trimmedLines.append(trimmedLine)
	print_line('getFileSystemUsage () trimmedLines=[{}]'.format(trimmedLines), debug=True)
	i = 0
	for currLine in trimmedLines:
		lineParts = currLine.split()
		print_line('lineParts({})=[{}]'.format(len(lineParts), lineParts), debug=True)
		if lineParts[5] == '/':
			disk_avail = float(lineParts[3])
			disk_used = float(lineParts[2])
			disk_usage = (disk_used / (disk_avail + disk_used) * 100)
			rpi_fs_used = '{:.1f}'.format(disk_usage)
			rpi_fs_space = '{:.0f}'.format(float(lineParts[1]) / 1024)
		else:
			rpi_fs_mount.append(lineParts[0] + ' > ' + lineParts[5])
	print_line('rpi_filesystem_size=[{}GB]'.format(rpi_fs_space), debug=True)
	print_line('rpi_filesystem_usage=[{}%]'.format(rpi_fs_used), debug=True)
	print_line('rpi_filesystem_mounted=[{}]'.format(rpi_fs_mount), debug=True)


#  -----------
#  getVcGenCmd
#  -----------
#  find location of vcgencmd
def getVcGenCmd():
	cmd_loc1 = '/usr/bin/vcgencmd'
	cmd_loc2 = '/opt/vc/bin/vcgencmd'
	desiredCommand = cmd_loc1
	if os.path.exists(desiredCommand) == False:
		desiredCommand = cmd_loc2
	if os.path.exists(desiredCommand) == False:
		desiredCommand = ''
	if desiredCommand != '':
		print_line('Found vcgencmd(1)=[{}]'.format(desiredCommand), debug=True)
	else:
		print_line('vcgencmd not available! GPU temperature can not be reported')
	return desiredCommand


#  --------
#  hostname
#  --------
#  extract hostname and fqdn from "hostname -f" command
def getHostname():
	global rpi_hostname
	global rpi_fqdn
	cmdString = "/bin/hostname -f"
	out = subprocess.Popen(cmdString,
		shell = True,
		stdout=subprocess.PIPE,
		stderr=subprocess.STDOUT)
	stdout,_ = out.communicate()
	fqdn_raw = stdout.decode('utf-8').rstrip()
	print_line('fqdn_raw=[{}]'.format(fqdn_raw), debug=True)
	rpi_hostname = fqdn_raw
	if '.' in fqdn_raw:
		#  have good fqdn
		nameParts = fqdn_raw.split('.')
		rpi_fqdn = fqdn_raw
		rpi_hostname = nameParts[0]
	else:
		#  missing domain, if we have a fallback from the configuration file, appply it
		if (len(fallback_domain) > 0):
			rpi_fqdn = '{}.{}'.format(fqdn_raw, fallback_domain)
		else:
			rpi_fqdn = rpi_hostname
	print_line('rpi_fqdn=[{}]'.format(rpi_fqdn), debug=True)
	print_line('rpi_hostname=[{}]'.format(rpi_hostname), debug=True)


#  --------------------
#  getNetworkIFsUsingIP
#  --------------------
#  use command "ip -br addr show" and "ip -br link show" to get interface status (up/down),
#  MAC and IP addresses
def getNetworkIFsUsingIP():
	global rpi_interfaces
	global rpi_mac
	cmdString1 = 'ip -br addr show | /bin/egrep "eth0 |wlan0"'
	cmdString2 = 'ip -br link show | /bin/egrep "eth0 |wlan0"'
	out = subprocess.Popen(cmdString1,
		shell=True,
		stdout=subprocess.PIPE,
		stderr=subprocess.STDOUT)
	stdout,_ = out.communicate()
	lines1 = stdout.decode('utf-8').split("\n")
	out = subprocess.Popen(cmdString2,
		shell=True,
		stdout=subprocess.PIPE,
		stderr=subprocess.STDOUT)
	stdout,_ = out.communicate()
	lines2 = stdout.decode('utf-8').split("\n")
	
	tmpInterfaces = []
	line_count = len(lines1) - 1
	if (line_count == 0 or line_count > 2):
		print_line('ERROR by ip(8) filter!', error=True)
		sys.exit(1)

	for lineIdx in range(line_count):
		trimmedLine1 = lines1[lineIdx].lstrip().rstrip()
		trimmedLine2 = lines2[lineIdx].lstrip().rstrip()
		if len(trimmedLine1) > 0:
			lineParts1 = trimmedLine1.split()
			lineParts2 = trimmedLine2.split()
			interfaceName = lineParts1[0]
			macAddress = lineParts2[2]
			if len(lineParts1) > 2:
				newTuple = (interfaceName, 'IP', lineParts1[2])
				tmpInterfaces.append(newTuple)
			newTuple = (interfaceName, 'mac', lineParts2[2])
			if rpi_mac == '':
				rpi_mac = lineParts2[2]
			tmpInterfaces.append(newTuple)
	rpi_interfaces = tmpInterfaces
	print_line('rpi_interfaces=[{}]'.format(rpi_interfaces), debug=True)
	print_line('rpi_mac=[{}]'.format(rpi_mac), debug=True)


#  ---------------------
#  getSystemTermperature
#  ---------------------
#
def getSystemTemperature():
	global rpi_gpu_temp
	global rpi_cpu_temp
	rpi_gpu_temp_raw = 'failed'
	cmd_fspec = getVcGenCmd()
	if cmd_fspec == '':
		rpi_gpu_temp = '{:.1f}'.format(float('-1.0'))
	else:
		retry_count = 3
		while retry_count > 0 and 'failed' in rpi_gpu_temp_raw:
			cmdString = "{} measure_temp".format(cmd_fspec)
			out = subprocess.Popen(cmdString,
				shell=True,
				stdout=subprocess.PIPE,
				stderr=subprocess.STDOUT)
			stdout,_ = out.communicate()
			rpi_gpu_temp_raw = stdout.decode('utf-8').rstrip().replace('temp=', '').replace('\'C', '')
			retry_count -= 1
			sleep(1)
	if not 'failed' in rpi_gpu_temp_raw:
		rpi_gpu_temp = '{:.1f}'.format(float(rpi_gpu_temp_raw))
	print_line('rpi_gpu_temp=[{}]'.format(rpi_gpu_temp), debug=True)
	cmdString = "/bin/cat /sys/class/thermal/thermal_zone0/temp"
	out = subprocess.Popen(cmdString,
		shell=True,
		stdout=subprocess.PIPE,
		stderr=subprocess.STDOUT)
	stdout,_ = out.communicate()
	rpi_cpu_temp_raw = stdout.decode('utf-8').rstrip()
	rpi_cpu_temp = '{:.1f}'.format(float(rpi_cpu_temp_raw) / 1000.0)
	print_line('rpi_cpu_temp=[{}]'.format(rpi_cpu_temp), debug=True)


#  ------
#  uptime
#  ------
#  use command "/usr/bin/uptime" to get time, Raspberry is up and running, extract data and present
#  it in the form "12d 5h:12m", respectively "5h:12m", if uptime is less than 24 hours
#  get values for CPU usage (1 minute and 5 minute running average)
def getUptime():
	global rpi_uptime_raw
	global rpi_uptime
	global rpi_cpu_usage_1m
	global rpi_cpu_usage_5m
	cmdString = "/usr/bin/uptime"
	out = subprocess.Popen(cmdString,
		shell=True,
		stdout=subprocess.PIPE,
		stderr=subprocess.STDOUT)
	stdout,_ = out.communicate()
	rpi_uptime_raw = stdout.decode('utf-8').rstrip().lstrip()
	print_line('rpi_uptime_raw=[{}]'.format(rpi_uptime_raw), debug=True)
	basicParts = rpi_uptime_raw.split()
	timeStamp = basicParts[0]
	lineParts = rpi_uptime_raw.split(',')
	rpi_cpu_usage_1m = '{:.1f}'.format(float(lineParts[3].replace('load average:', '')\
		.replace(',', '').lstrip().rstrip()) / rpi_nbrCPUs * 100)
	rpi_cpu_usage_5m = '{:.1f}'.format(float(lineParts[4].replace(',', '').lstrip().rstrip()) / rpi_nbrCPUs * 100)
	print_line('rpi_cpu_usage_1m=[{}%]'.format(rpi_cpu_usage_1m), debug=True)
	print_line('rpi_cpu_usage_5m=[{}%]'.format(rpi_cpu_usage_5m), debug=True)
	if 'user' in lineParts[1]:
		rpi_uptime_raw = lineParts[0].replace(timeStamp, '').lstrip().replace('up ', '')
		timeParts = rpi_uptime_raw.split(':')
		if len(timeParts) == 1:
			# rpi_uptime_raw = timeParts[0].lstrip()+'m'
			timeParts[0] = timeParts[0].replace('min', '').lstrip().rstrip()
			rpi_uptime = timeParts[0]+'m'
		else:
			rpi_uptime = timeParts[0].lstrip()+'h'+timeParts[1].rstrip()+'m'
	else:
		lineParts[0] = lineParts[0].replace(timeStamp, '').lstrip().replace('up ', '').\
		replace('day', '').replace('s', '').rstrip()
		timeParts = lineParts[1].split(':')
		if len(timeParts) == 1:
			timeParts[0] = timeParts[0].replace('min', '').lstrip().rstrip()
			rpi_uptime = lineParts[0]+'d '+timeParts[0]+'m'
		else:
			rpi_uptime = lineParts[0]+'d '+timeParts[0].lstrip()+'h'+timeParts[1].rstrip()+'m'
	print_line('rpi_uptime=[{}]'.format(rpi_uptime), debug=True)


#  ----------------------------------------------------------------
#  last update date and time passed since, status "rpi_security[0]"
#  ----------------------------------------------------------------
def getLastUpdateDate():
	global rpi_lastUpdateDate
	global rpi_timesincelastUpdateDate
	global rpi_security
	#  apt-get update writes to following directory (so date changes on update)
	apt_listdir_filespec = '/var/lib/apt/lists/partial'
	updateModDateInSeconds = os.path.getmtime(apt_listdir_filespec)
	rpi_lastUpdateDate = datetime.fromtimestamp(updateModDateInSeconds).strftime('%-d-%m-%Y %H:%M:%S')
	print_line('rpi_lastUpdateDate=[{}]'.format(rpi_lastUpdateDate), debug=True)
	timeNowInSeconds = time()
	timesincelastUpdate = int(timeNowInSeconds - updateModDateInSeconds)
	rpi_timesincelastUpdateDate = getdeltatime(timesincelastUpdate)
	print_line('rpi_timesincelastUpdateDate=[{}]'.format(rpi_timesincelastUpdateDate), debug=True)

	rpi_security[0][1] = 'safe'
	if (timesincelastUpdate > maxTimesinceUpdate):
		rpi_security[0][1] = 'warning'
	print_line('rpi_update_status=[{}]'.format(rpi_security[0]), debug=True)


#  -----------------------------------------------------------------
#  last upgrade date and time passed since, status "rpi_security[1]"
#  -----------------------------------------------------------------
def getLastUpgradeDate():
	global rpi_lastUpgradeDate
	global rpi_timesincelastUpgradeDate
	global rpi_security
	#  apt-get upgrade | autoremove update the following file when actions are taken
	apt_lockdir_filespec = '/var/lib/dpkg/lock'
	upgradeModDateInSeconds = os.path.getmtime(apt_lockdir_filespec)
	rpi_lastUpgradeDate = datetime.fromtimestamp(upgradeModDateInSeconds).strftime('%-d-%m-%Y %H:%M:%S')
	print_line('rpi_lastUpdateDate=[{}]'.format(rpi_lastUpdateDate), debug=True)
	timeNowInSeconds = time()
	timesincelastUpgrade = int(timeNowInSeconds - upgradeModDateInSeconds)
	rpi_timesincelastUpgradeDate = getdeltatime(timesincelastUpgrade)
	print_line('rpi_timesincelastUpgradeDate=[{}]'.format(rpi_timesincelastUpgradeDate), debug=True)

	rpi_security[1][1] = 'safe'
	if (timesincelastUpgrade > maxTimesinceUpgrade):
		rpi_security[1][1] = 'warning'
	print_line('rpi_upgrade_status=[{}]'.format(rpi_security[1]), debug=True)


#  -----------------------------------------------------
#  get deltatime formatted from seconds into xd yhzm
#  -----------------------------------------------------
def getdeltatime(deltatime):
	days = deltatime // 86400
	rest1 = deltatime - (days * 86400)
	hours = rest1 // 3600
	rest2 = rest1 - (hours * 3600)
	minutes = rest2 // 60
	if days != 0 and hours != 0:
		deltatime = str(days)+'d '+str(hours)+'h'+str('{:02d}'.format(minutes))+'m'
	elif days != 0 and hours == 0:
		deltatime = str(days)+'d '+str(minutes)+'m'
	elif days == 0 and hours == 0:
		deltatime = str(minutes)+'m'
	else:
		deltatime = str(hours)+'h'+str('{:02d}'.format(minutes))+'m'
	return deltatime


#  -----------------
#  last install date
#  -----------------
#  extract vom file "/var/log/dpkg.log" date of last "status installed"
#def getLastInstallDate():
#	global rpi_last_install_date
#	cmdString = "/bin/grep --binary-files=text 'status installed' /var/log/dpkg.log 2>/dev/null | sort | tail -1"
#	out = subprocess.Popen(cmdString,
#		shell=True,
#		stdout=subprocess.PIPE,
#		stderr=subprocess.STDOUT)
#	stdout,_ = out.communicate()
#	last_installed_pkg_raw = stdout.decode('utf').rstrip().lstrip()
#	print_line('last_installed_pkg_raw=[{}]'.format(last_installed_pkg_raw), debug=True)
#	lineParts = last_installed_pkg_raw.split()
#	if len(lineParts) > 1:
#		pkg_date_string = '{} {}'.format(lineParts[0], lineParts[1])
#		pkg_install_date = datetime.striptime(pkg_date_string, '%d-%m-%Y %H:%M:%S')
#		rpi_last_install_date = pkg_install_date
#	print_line('rpi_last_install_date=[{}]'.format(rpi_last_install_date), debug=True)


#  get hostnames to setup MQTT
getHostname()
if sensor_name == default_sensor_name:
	sensor_name = 'rpi-{}'.format(rpi_hostname)
#  get model so we can use it in MQTT
getDeviceModel()
getNbrCPUs()
getDeviceCpuModel()
getOSandKernelVersion()
getFileSystemUsage()


#  -----------------------------------------------------
#  timer and timer funcs for ALIVE MQTT notices handling
#  -----------------------------------------------------
ALIVE_TIMEOUT_IN_SECONDS = 60

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
	aliveTimerRunningsStatus = False
	print_line('- stopped MQTT timer', debug=True)

def isAliveTimerRunning():
	global aliveTimerRunningStatus
	return aliveTimerRunningStatus

#  our ALIVE TIMER
aliveTimer = threading.Timer(ALIVE_TIMEOUT_IN_SECONDS, aliveTimeoutHandler)
#  our BOOL tracking state of ALIVE TIMER
aliveTimerRunningStatus = False


#  ----------------------
#  MQTT setup and startup
#  ----------------------

#  MQTT connection
lwt_topic = '{}/sensor/{}/status'.format(base_topic, sensor_name.lower())
lwt_online_val = 'online'
lwt_offline_val = 'offline'

print_line('Connecting to MQTT broker ...', verbose=True)
mqtt_client = mqtt.Client()
mqtt_client.on_connect = on_connect
mqtt_client.on_publish = on_publish

mqtt_client.will_set(lwt_topic, payload=lwt_offline_val, retain=True)
#mqtt_client.will_set(lwt_topic2, payload=lwt_offline_val, retain=True)

if config['MQTT'].getboolean('tls', False):
	mqtt_client.tls_set(
		ca_certs=config['MQTT'].get('tls_a_cert', None),
		keyfile=config['MQTT'].get('tls_keyfile', None),
		certfile=config['MQTT'].get('tls_certfile', None),
		tls_version=ssl.PROTOCOL_SSLv23)

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
	#  add additional line for connection to binary_sensor
	#mqtt_client.publish(lwt_topic2, payload=lwt_online_val, retain=False)
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
getNetworkIFsUsingIP()         #  this will fill-in rpi_mac

mac_basic = rpi_mac.lower().replace(":", "")
mac_left = mac_basic[:6]
mac_right = mac_basic[6:]
print_line('mac lt=[{}],  rt=[{}], mac=[{}]'.format(mac_left, mac_right, mac_basic), debug=True)
uniqID = "RPi-{}Mon{}".format(mac_left, mac_right)

#  Raspberry Pi (rpi) monitor device with 6 sensors and 1 binary sensor
LD_MONITOR = "monitor"    			#  sensor
LD_CPU_TEMP = "temp_cpu_c"    		#  sensor
LD_FS_USED = "disk_used"    		#  sensor
LD_CPU_USAGE_1M = "cpu_load_1m"     #  sensor 
LD_CPU_USAGE_5M = "cpu_load_5m"	    #  sensor
LD_MEM_USED = "ram_used_prcnt"		#  sensor
LD_SECURITY_STATUS = "os_security_status"	#  binary_sensor
LDS_PAYLOAD_NAME = "info"

#  Publish MQTT auto discovery ....
#  table of key items to be published for sensors:
detectorValues = OrderedDict([
	(LD_MONITOR, dict(title="{} RPi Monitor".format(rpi_hostname), device_class="timestamp", \
		no_title_prefix="yes", json_value="Timestamp", json_attr="yes", icon='mdi:raspberry-pi', \
		device_ident='Raspberry Pi {}'.format(rpi_hostname.title()))),
	(LD_CPU_TEMP, dict(title="{} CPU Temp".format(rpi_hostname), device_class="temperature", \
		no_title_prefix="yes", unit="°C", json_value="Temp_CPU_c", icon='mdi:thermometer')),
	(LD_CPU_USAGE_1M, dict(title="{} CPU Load (1 min)".format(rpi_hostname.title()), no_title_prefix="yes", \
		json_value="CPU_Load_1_min", unit="%", icon='mdi:chip')),
	(LD_CPU_USAGE_5M, dict(title="{} CPU Load (5 min)".format(rpi_hostname.title()), no_title_prefix="yes", \
		json_value="CPU_Load_5_min", unit="%", icon='mdi:chip')),
	(LD_MEM_USED, dict(title="{} Memory Usage".format(rpi_hostname), no_title_prefix="yes", \
		json_value="RAM_used_prcnt", unit="%", icon='mdi:memory')),
	(LD_FS_USED, dict(title="{} Disk Usage".format(rpi_hostname), no_title_prefix="yes", \
		json_value="FS_used_prcnt", unit="%", icon='mdi:sd')),
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
			'sw_version' : "{} {}".format(rpi_os, rpi_os_version)
		}
	else:
		payload['dev'] = {
			'identifiers' : ["{}".format(uniqID)],
		}

	mqtt_client.publish(discovery_topic, json.dumps(payload), 1, retain=True)


#  auto-discovery of binary_sensor
discovery_topic = '{}/binary_sensor/{}/config'.format(discovery_prefix, \
	sensor_name.lower())
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

TIMER_INTERRUPT = (-1)
TEST_INTERRUPT = (-2)

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
RPI_MODEL = "Raspberry_Model"
RPI_HOSTNAME = "Hostname"
RPI_FQDN = "Fqdn"
RPI_OS = "OS"
RPI_OS_VERSION = "OS_Version"
RPI_UPTIME = "Up_time"
RPI_OS_LAST_UPDATE = "OS_Last_Update"
RPI_OS_LAST_UPGRADE = "OS_Last_Upgrade"
RPI_FS_SPACE = "FS_total_gb"
RPI_FS_USED = "FS_used_prcnt"
RPI_FS_MOUNT = "FS_mounted"
RPI_RAM_USED = "RAM_used_prcnt"
RPI_CPU_TEMP = "Temp_CPU_c"
RPI_CPU_USED_1M = "CPU_Load_1_min"
RPI_CPU_USED_5M = "CPU_Load_5_min"
RPI_GPU_TEMP = "Temp_GPU_c"
RPI_SCRIPT = "Reporter"
RPI_NETWORK = "Network Interfaces"
RPI_OS_UPDATE = rpi_security[0][0]
RPI_OS_UPGRADE = rpi_security[1][0]
RPI_SECURITY_STATUS = "Security_Status"
# tupel cpu (mode name, #cores, serial#)
RPI_CPU = "CPU"
RPI_CPU_MODEL = "Model"
RPI_CPU_CORES = "Cores"
#RPI_CPU_BOGOMIPS = "BogoMIPS"
RPI_CPU_SERIAL = "Serial"
SCRIPT_REPORT_INTERVAL = "Reporter_Interval [min]"


def send_status(timestamp, nothing):
	# prepare and send update of sensor data
	global rpi_security_status
	rpiData = OrderedDict()
	rpiData[SCRIPT_TIMESTAMP] = timestamp.astimezone().replace(microsecond=0).isoformat()
	rpiData[RPI_MODEL] = rpi_model
	rpiData[RPI_HOSTNAME] = rpi_hostname
	rpiData[RPI_FQDN] = rpi_fqdn
	rpiData[RPI_OS] = rpi_os
	rpiData[RPI_OS_VERSION] = rpi_os_version
	rpiData[RPI_OS_LAST_UPDATE] = rpi_timesincelastUpdateDate+' ago - '+rpi_security[0][1]
	rpiData[RPI_OS_LAST_UPGRADE] = rpi_timesincelastUpgradeDate+' ago - '+rpi_security[1][1]
	rpiData[RPI_UPTIME] = rpi_uptime

	rpiData[RPI_FS_SPACE] = int(rpi_fs_space)
	rpiData[RPI_FS_USED] = rpi_fs_used
	rpiData[RPI_RAM_USED] = rpi_ram_used

	rpiData[RPI_CPU_TEMP] = rpi_cpu_temp
	rpiData[RPI_GPU_TEMP] = rpi_gpu_temp
	rpiData[RPI_CPU_USED_1M] = rpi_cpu_usage_1m
	rpiData[RPI_CPU_USED_5M] = rpi_cpu_usage_5m

	rpiData[RPI_SCRIPT] = rpi_mqtt_script
	rpiData[SCRIPT_REPORT_INTERVAL] = interval_in_minutes

	rpiCpu = getCPUDictionary()
	if len(rpiCpu) > 0:
		rpiData[RPI_CPU] = rpiCpu

	rpiData[RPI_FS_MOUNT] = getFSmountDictionary()

	rpiData[RPI_NETWORK] = getNetworkDictionary()
	
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

	#else:
	#	change = True
	#	for i in range(len(rpi_security)):
	#		if rpi_security[i][1] != 'safe':
	#			change = False
	#			break
	#	if change:
	#		rpi_security_status = 'off'
	#		topic = "home/nodes/binary_sensor/{}/status".format(sensor_name.lower())
	#		_thread.start_new_thread(publishSecurityStatus, ('off', topic))


def getCPUDictionary():
	#  tuple (modelname, #cores, serial#)
	cpuDict = OrderedDict()
	#rpi_cpu_tuple = ( cpu_model, cpu_cores, cpu_serial )
	if rpi_cpu_tuple != '':
		cpuDict[RPI_CPU_MODEL] = rpi_cpu_tuple[0]
		cpuDict[RPI_CPU_CORES] = rpi_cpu_tuple[1]
#		cpuDict[RPI_CPU_BOGOMIPS] = int(rpi_cpu_tuple[2], 10)
		cpuDict[RPI_CPU_SERIAL] = rpi_cpu_tuple[2]
	return cpuDict

def getFSmountDictionary():
	fsmountDict = OrderedDict()
	i = 0
	if len(rpi_fs_mount) == 0:
		fsmountDict['none'] =''
	else:
		for i in range(len(rpi_fs_mount)):
			fsmountDict[''] = rpi_fs_mount[i]
	print_line('fsmountDict:{}'.format(fsmountDict), debug=True)
	return fsmountDict


def getNetworkDictionary():
	networkDict = OrderedDict()
	priorIFKey = ''
	tmpData = OrderedDict()
	for currTuple in rpi_interfaces:
		currIFKey = currTuple[0]
		if priorIFKey == '':
			priorIFKey = currIFKey
		if currIFKey != priorIFKey:
			if priorIFKey != '':
				networkDict[priorIFKey] = tmpData
				tmpData = OrderedDict()
				priorIFKey = currIFKey
		subKey = currTuple[1]
		subValue = currTuple[2]
		tmpData[subKey] = subValue
	networkDict[priorIFKey] = tmpData
	print_line('networkDict:{}'.format(networkDict), debug=True)
	return networkDict


def publishMonitorData(latestData, topic):
	print_line('Publishing to MQTT topic  "{}, Data:{}"'.format(topic, json.dumps(latestData)))
	mqtt_client.publish('{}'.format(topic), json.dumps(latestData), 1, retain=False)
	sleep(0.5)


def publishSecurityStatus(status, topic):
	print_line('Publishing to MQTT topic "{}, Data:{}"'.format(topic, status))
	mqtt_client.publish('{}'.format(topic), payload='{}'.format(status), retain=False)
	sleep(0.5)

def update_values():
	getUptime()
	getSystemTemperature()
	getLastUpdateDate()
	getLastUpgradeDate()
	getDeviceMemUsage()

#  ---------------------------------------------------------------

def handle_interrupt(channel):
	global reported_first_time
	sourceID = "<< INTR(" + str(channel) + ")"
	current_timestamp = datetime.now(local_tz)
	print_line(sourceID + " >> Time to report! {}".format(current_timestamp.strftime('%H:%M:%S - %Y/%m/%d')), \
		verbose=True)
	update_values()
	if (opt_stall == False or reported_first_time == False and opt_stall == True):
		#  report our new detection to MQTT
		_thread.start_new_thread(send_status, (current_timestamp, ''))
		reported_first_time = True
	else:
		print_line(sourceID + " >> Time to report! {} but SKIPPED (Test: stall)".format(\
			current_tiemstamp.strftime('%H:%M:%S - %Y/%m/%d')), verbose=True)


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


