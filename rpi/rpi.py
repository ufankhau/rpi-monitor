#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import subprocess
from collections import OrderedDict
from time import time


#  ***************
#  Helper Routines
def next_power_of_two(x: int):
	"""
	Calculate smallest power of 2 greater than or equal to x. Reduce result to < 1024 by
	deviding it by 1024 as often as possible. Return in a tuple last quotient together with the
	number of divisions possible.

	Examples:

	next_power_of_two(768) returns (1, 1)  --> 1024 is next power of 2 for the input x=768. This can be devided once by 1024 with a resulting quotient of 1, hence the return tuple of (1, 1)

	next_power_of_two(468349) returns (512, 1) --> 524288 is next power of 2 for the input x=468349. Devided by 1024 gives a quotient of 512, hence the return tuple of (512, 1)

	next_power_of_two(114638784) returns (128, 2) --> 134217728 is next power of 2 for the input x=114638784. This can be devided twice by 1024 with a quotient of 128, hence the return tuple of (128, 2)
	"""
	if x == 0:
		res = 1
	else:
		res = 1 << (x - 1).bit_length()
	magnitude = 0
	while res >= 1024:
		res /= 1024
		magnitude += 1
	return (int(res), magnitude)


def get_command_location(arg: str):
	"""
	Return the location of the command 'arg' as a string. If 'arg' is not found, return empty
	string.
	"""
	cmd_string = "/usr/bin/which " + arg
	out = subprocess.Popen(cmd_string, 
												 shell=True, 
												 stdout=subprocess.PIPE, 
												 stderr=subprocess.STDOUT)
	stdout, _ = out.communicate()
	loc = stdout.decode("utf-8").strip()
	return "" if loc == "" or "not found" in loc else loc


def format_seconds(num: int):
	"""
	Format integer received as seconds into string of day(s) hour(s) and minute(s).

	Examples:

		93845	--> 	1d 2h04m

		434220 --> 	5d 37m
	"""
	days = num // 86400
	hours = (num - days * 86400) // 3600
	minutes = (num - days * 86400 - hours * 3600) // 60

	if days != 0 and hours != 0:
		deltatime = "{}d {}h{:02d}m".format(days, hours, minutes)
	elif days != 0 and hours == 0:
		deltatime = "{}d {}m".format(days, minutes)
	elif days == 0 and hours == 0:
		deltatime = "{}m".format(minutes)
	else:
		deltatime = "{}h{:02d}m".format(hours, minutes)
	return deltatime


#  Get Locations of Executables on the Filesystem of the Raspberry Pi
cat = get_command_location("cat")
awk = get_command_location("awk")
egrep = get_command_location("egrep")
cut = get_command_location("cut")
tail = get_command_location("tail")
hostname = get_command_location("hostname")
lscpu = get_command_location("lscpu")
df = get_command_location("df")
uname = get_command_location("uname")
ip = get_command_location("ip")
uptime = get_command_location("uptime")
getconf = get_command_location("getconf")


#  *******************************************************************
#  Set of Functions to Retrieve Static Information from a Raspberry Pi
def get_device_model():
	"""
	Return string with device model of Raspberry Pi
	"""
	cmd_string = "{} -n1 /proc/cpuinfo | {} -F': ' '{print $2}'".format(tail, awk)
	out = subprocess.Popen(cmd_string,
                          shell=True, 
						  stdout=subprocess.PIPE, 
						  stderr=subprocess.STDOUT)
	stdout, _ = out.communicate()
	rpi_model_raw = stdout.decode("utf-8").strip()
	return (rpi_model_raw.replace(" Model ", "")
					.replace(" Plus ", "+")
					.replace("Rev ", " r")
					.replace("\n", ""))


def get_hostname():
	"""
	Return 'hostname' and 'fqdn' of the Raspberry PI in a tupple (hostname, fqdn)
	"""
	cmd = get_command_location("hostname")
	cmd_string = "{} -f".format(cmd)
	out = subprocess.Popen(cmd_string,
                           shell=True,
						   stdout=subprocess.PIPE, 
						   stderr=subprocess.STDOUT)
	stdout, _ = out.communicate()
	fqdn = stdout.decode("utf-8").rstrip()
	if "." in fqdn:
		#  have good fqdn
		hostname = fqdn.split(".")[0]
	else:
		hostname = fqdn

	return (hostname, fqdn)


def get_device_cpu_info():
	"""
	Return static data of the CPU in a dictionary with the following content:

		- architecture ["Architecture"]
		- number of cores ["Core(s)"]
		- model (vendor, name, release) ["Model"]
		- clock speed (min | max) ["Core Speed [MHz] (min|max)"]
		- serial number ["Serial"]
	"""
	cpu_info = OrderedDict()
	cmd_string1 = "{} | {} -i 'architecture|core\(s\)|vendor|model|min|max'".format(lscpu, egrep)
	cmd_string2 = "{} /proc/cpuinfo | {} -i 'serial' | {} -F': ' '{print $2}'".format(cat, egrep, awk)
	out = subprocess.Popen(cmd_string1, 
                           shell=True,
						   stdout=subprocess.PIPE, 
						   stderr=subprocess.STDOUT)
	stdout, _ = out.communicate()
	lines = stdout.decode("utf-8").split("\n")
	trimmed_lines = []
	for curr_line in lines:
		trimmed_line = curr_line.strip()
		trimmed_lines.append(trimmed_line)
	for curr_line in trimmed_lines:
		line_parts = curr_line.split(":")
		# currValue = '{?unk?}'
		if len(line_parts) >= 2:
			curr_value = line_parts[1].strip()
		if "Architecture" in curr_line:
			cpu_info["Architecture"] = curr_value
		if "Core(s)" in curr_line:
			cpu_info["Core(s)"] = int(curr_value)
		if "Vendor" in curr_line:
			cpu_vendor = curr_value
		if "Model:" in curr_line:
			cpu_model = curr_value
		if "Model name" in curr_line:
			cpu_model_name = curr_value
		if "CPU max" in curr_line:
			cpu_clock_speed_max = "{:.0f}".format(float(curr_value))
		if "CPU min" in curr_line:
			cpu_clock_speed_min = "{:.0f}".format(float(curr_value))
	
	# build CPU model name ....
	if cpu_model_name.find(cpu_vendor) >= 0:
		cpu_info["Model"] = "{} r{}".format(cpu_model_name, cpu_model)
	else:
		cpu_info["Model"] = "{} {} r{}".format(cpu_vendor, cpu_model_name, cpu_model)
	
	# build clock speed info ....
	cpu_info["Clock Speed [MHz] (min|max)"] = "{} | {}".format(cpu_clock_speed_min, cpu_clock_speed_max)
	
	# get serial number
	out = subprocess.Popen(cmd_string2,
			               shell=True,
			               stdout=subprocess.PIPE,
			               stderr=subprocess.STDOUT)
	stdout, _ = out.communicate()
	cpu_info["Serial"] = stdout.decode("utf-8").strip()

	return cpu_info


def get_device_ram_installed():
    """
    Return installed RAM on the Raspberry Pi in form of a tuple of two integers.
    The first value is the RAM size. The second value is an index for the units to be applied
    (0 = 'kB', 1 = 'MB', 2 = 'GB', 3 = 'TB').

    Example:

    (64, 2) = RAM size of 64 GB \n
    (512, 1) = RAM size of 512 kB

    The function uses the helper routine next_power_of_two().
    """
    cmd_string = "{} /proc/meminfo | {} -i 'memtotal' | {} '{print $2}'".format(cat, egrep, awk)
    out = subprocess.Popen(cmd_string,
			               shell=True,
			               stdout=subprocess.PIPE,
			               stderr=subprocess.STDOUT)
    stdout, _ = out.communicate()
    return next_power_of_two(int(stdout.decode("utf-8").strip()))


def get_filesystem_size():
    """
    Return size of the filesystem in form of a tuple of two integers. The first value
    is the size, the second value an index for the units to be applied (0 = 'kB', 1 = 'MB', 2 = 'GB', 3 = 'TB')

    The function uses the helper routine next_power_of_two().
    """
    cmd_string = "{} -k | {} -n +2 | {} -i 'root' | {} '{print $2}'".format(df, tail, egrep, awk)
    out = subprocess.Popen(cmd_string,
			               shell=True,
			               stdout=subprocess.PIPE,
			               stderr=subprocess.STDOUT)
    stdout, _ = out.communicate()
    return next_power_of_two(int(stdout.decode("utf-8").strip()))


def get_filesystems_mounted():
    """
    Return list of filesystem(s) mounted to the Raspberry Pi. Each item in the list represents
    a mounted drive in the form of "mounted device, mount point".
    """
    fs_mounted = [str]
    cmd_string = "{} | {} -n +2 | {} -v 'tmpfs|boot|root|overlay|udev'".format(df, tail, egrep)
    out = subprocess.Popen(cmd_string,
			               shell=True,
			               stdout=subprocess.PIPE,
			               stderr=subprocess.STDOUT)
    stdout, _ = out.communicate()
    lines = stdout.decode("utf-8").split("\n")
    for line in lines:
        line_parts = line.split()
        fs_mounted.append("{}, {}".format(line_parts[0], line_parts[5]))
    return fs_mounted


def get_os_bit_length():
	"""
	Return 32 or 64 bit length of OS system.
    """
	cmd_string = "{} LONG_BIT".format(getconf)
	out = subprocess.Popen(cmd_string,
			               shell=True,
			               stdout=subprocess.PIPE,
			               stderr=subprocess.STDOUT)
	stdout, _ = out.communicate()
	return int(stdout.decode('utf-8').strip())


def get_os_release():
    """
    Return name of OS/Linux release as a string.
    """
    cmd_string = "{} /etc/os-release | {} -i 'pretty_name' | {} -F'\"' '{print $2}'".format(cat, egrep, awk)
    out = subprocess.Popen(cmd_string,
			               shell=True,
			               stdout=subprocess.PIPE,
			               stderr=subprocess.STDOUT)
    stdout, _ = out.communicate()
    return stdout.decode("utf-8").strip()


def get_os_version():
    """
    Return OS kernel version as a string.
    """
    cmd_string = "{} -r".format(uname)
    out = subprocess.Popen(cmd_string,
			               shell=True,
			               stdout=subprocess.PIPE,
			               stderr=subprocess.STDOUT)
    stdout, _ = out.communicate()
    return stdout.decode("utf-8").rstrip()


def get_network_interfaces():
    """
    Return tuple of nested ordered dictionaries and a string. The nested dictionaries
    describe the enabled physical network interfaces on a Raspberry Pi, listing for each
    interface its IP and MAC address (if allocated), where the string returns the MAC address of the first physical	interface in lower characters.
    """
    mac_address = ""
    cmd_string = "{} addr show | {} 'eth0:|wlan0:' | {} '{print $2}' | {} -d':' -f1".format(
	    ip, egrep, awk, cut)
    out = subprocess.Popen(cmd_string,
			               shell=True,
			               stdout=subprocess.PIPE,
			               stderr=subprocess.STDOUT)
    stdout, _ = out.communicate()
    iface_names = stdout.decode("utf-8").split()

    # loop over 'iface_names' and build OrderedDict 'interfaces'
    interfaces = OrderedDict()
    for idx in iface_names:
        interface = OrderedDict()
        # get IP4 address
        cmd_IP = "{} -4 addr show {} | {} inet | {} '{print $2}' | {} -d'/' -f1".format(
		ip, idx, egrep, awk, cut)
        out = subprocess.Popen(cmd_IP,
			                   shell=True,
				               stdout=subprocess.PIPE,
				               stderr=subprocess.STDOUT)
        stdout, _ = out.communicate()
        ip = stdout.decode("utf-8").strip()
        if not ip == "":
            interface["IP"] = ip

        # get MAC address
        cmd_MAC = "{} link show {} | {} ether | {} '{print $2}'".format(
            ip, idx, egrep, awk)
        out = subprocess.Popen(cmd_MAC,
			                   shell=True,
				               stdout=subprocess.PIPE,
				               stderr=subprocess.STDOUT)
        stdout, _ = out.communicate()
        mac = stdout.decode("utf-8").upper().strip()
        if not mac == "":
            interface["MAC"] = mac
            if mac_address == "":
                mac_address = mac.lower()

        interfaces[idx] = interface
    return (interfaces, mac_address)


#  ********************************************************************
#  Set of Functions to Retrieve Dynamic Information from a Raspberry Pi
def get_device_ram_used():
    """
    Return integer with amount of RAM used on a Raspberry Pi in range [0...100]
    """
    cmd_string = "{} /proc/meminfo | {} -i 'mem[tf]'".format(cat, egrep)
    out = subprocess.Popen(cmd_string,
			               shell=True,
			               stdout=subprocess.PIPE,
			               stderr=subprocess.STDOUT)
    stdout, _ = out.communicate()
    lines = stdout.decode("utf-8").split("\n")
    for line in lines:
        if "MemTotal" in line:
            mem_total = int(line.split()[1].strip())
        if "MemFree" in line:
            mem_free = int(line.split()[1].strip())
    return round((mem_total - mem_free) / mem_total * 100)


def get_device_temperatures():
    """
    Return tuple of 2 float values with actual CPU and GPU temperature. GPU temperature
    is set to -1.0 in case the utilty program 'vcgencmd' is missing on the Raspberry Pi.
    """
    # get CPU temperature
    cmd_string = "{} /sys/class/thermal/thermal_zone0/temp".format(cat)
    out = subprocess.Popen(cmd_string,
			               shell=True,
			               stdout=subprocess.PIPE,
			               stderr=subprocess.STDOUT)
    stdout, _ = out.communicate()
    cpu_temp = float(stdout.decode("utf-8").rstrip()) / 1000

    # get GPU temperature
    loc_vcgencmd = get_command_location("vcgencmd")
    if not loc_vcgencmd == "":
        cmd_string = "{} measure_temp | {} -F'=' '{print $1}' | {} -d\"'\" -f1".format(
            loc_vcgencmd, awk, cut)
        out = subprocess.Popen(cmd_string,
			                   shell=True,
				               stdout=subprocess.PIPE,
				               stderr=subprocess.STDOUT)
        stdout, _ = out.communicate()
        gpu_temp = float(stdout.decode("utf-8").strip())
    else:
        gpu_temp = -1.0

    return (cpu_temp, gpu_temp)


def get_uptime():
    """
    Return uptime of the Raspberry Pi in the format 'xd yhzm' as a string
    """
    cmd_string = "{} | {} -F'up ' '{print $2}' | {} -F'. user' '{print $1}'".format(
        uptime, awk, awk)
    out = subprocess.Popen(cmd_string,
			               shell=True,
			               stdout=subprocess.PIPE,
			               stderr=subprocess.STDOUT)
    stdout, _ = out.communicate()
    return (
        stdout.decode("utf-8")
        .replace(" days,", "d")
        .replace(";", "h")
        .replace(",", "m")
    )


def get_cpu_load():
    """
    Return tuple of three float numbers rounded to 1 decimal, representing the 1m, 5m and 15min
    average cpu load of the Raspberry Pi.
    """
    cmd_string = "{} /proc/loadavg".format(cat)
    out = subprocess.Popen(cmd_string,
			               shell=True,
			               stdout=subprocess.PIPE,
			               stderr=subprocess.STDOUT)
    stdout, _ = out.communicate()
    loadavg = stdout.decode("utf-8").split()[0:2]
    return (
        round(float(loadavg[0]) * 100, 1),
        round(float(loadavg[1]) * 100, 1),
        round(float(loadavg[2]) * 100, 1),
    )


def get_cpu_clock_speed():
    """
    Return current CPU clock speed as a float in MHz.
    """
    cmdString = "{} /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq".format(cat)
    out = subprocess.Popen(cmdString,
			               shell=True,
			               stdout=subprocess.PIPE,
			               stderr=subprocess.STDOUT)
    stdout, _ = out.communicate()
    return int(stdout.decode("utf-8").strip()) / 1000


def get_time_since_last_os_update():
    """
    Return seconds passed since last run of 'sudo apt-get update' command on Raspberry Pi.
    """
    #  'sudo apt-get update' writes to the following directory (so date changes on update)
    apt_listdir_filespec = "/var/lib/apt/lists/partial"
    date_last_update_run_in_seconds = os.path.getmtime(apt_listdir_filespec)
    date_now_in_seconds = time()
    return int(date_now_in_seconds - date_last_update_run_in_seconds)


def get_time_since_last_os_upgrade():
    """
    Return seconds passed since last upgrade applied to the operating system of the
    Raspberry Pi.
    """
    #  'sudo apt-get upgrade' updates the following file when actions are taken
    apt_lockdir_filespec = "/var/lib/dpkg/lock"
    date_last_upgrade_applied_in_seconds = os.path.getmtime(apt_lockdir_filespec)
    date_now_in_seconds = time()
    return int(date_now_in_seconds - date_last_upgrade_applied_in_seconds)
