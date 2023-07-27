# import the necessary packages
from .rpi import format_seconds, next_power_of_two
from .rpi import get_command_location
from .rpi import format_seconds


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

from .rpi import get_device_model
from .rpi import get_hostname
from .rpi import get_device_cpu_info
from .rpi import get_device_ram_installed
from .rpi import get_filesystem_size
from .rpi import get_filesystems_mounted
from .rpi import get_os_bit_length
from .rpi import get_os_release
from .rpi import get_os_version
from .rpi import get_network_interfaces

from .rpi import get_device_ram_used
from .rpi import get_device_temperatures
from .rpi import get_uptime
from .rpi import get_cpu_load
from .rpi import get_cpu_clock_speed
from .rpi import get_time_since_last_os_update
from .rpi import get_time_since_last_os_upgrade