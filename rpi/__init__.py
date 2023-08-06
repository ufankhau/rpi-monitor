# import the necessary packages
# helper functions
"""
Module provides a set of functions to retrieve static and dynamic information
of a Raspberry Pi such as:
- device name
"""

from .rpi import next_power_of_two
from .rpi import get_command_location

# functions to get static data
from .rpi import get_device_model
from .rpi import get_hostname
from .rpi import get_device_cpu_info
from .rpi import get_device_memory_installed
from .rpi import get_device_drive_size
from .rpi import get_drives_mounted
from .rpi import get_os_bit_length
from .rpi import get_os_release
from .rpi import get_os_version
from .rpi import get_network_interfaces

# functions to get dynamic data
from .rpi import get_cpu_load
from .rpi import get_cpu_clock_speed
from .rpi import get_device_drive_used
from .rpi import get_device_memory_used
from .rpi import get_device_temperatures
from .rpi import get_os_pending_updates
from .rpi import get_timestamp_of_last_os_upgrade_in_seconds
from .rpi import get_uptime
