#!/usr/bin/bash -e

# settings
name=$(hostname)
os=$(/usr/bin/cat /etc/os-release | /usr/bin/egrep -i 'pretty_name' | /usr/bin/awk -F'\"' '{print $2}')
logDIR=/home/pi/log
logfn=$logDIR/${name}-update-os.log
if [[ ! -d $logDIR ]]; then mkdir $logDIR; chown pi:pi $logDIR; fi 

# timestamp function
timestamp()
{
    date +%F_%T
}

# main body
echo "$(timestamp)" >> $logfin
echo "$(timestamp) *** upgrade ${os} on ${^name}" >> $logfn
echo "$(timestamp) ***" >> $logfn
/usr/bin/apt-get upgrade -y 2>$1 >> $logfn
if [[ $? = 0 ]]; then
    echo "$(timestamp) ***" >> $logfin
    echo "$(timestamp) *** upgrade successfully completed!" >> $logfin
    echo "$(timestamp)" >> $logfin
else
    echo "$(timestamp) ***" >> $logfin
    echo "$(timestamp) *** ERROR occured during upgrade, please check!" >> $logfin
    echo "$(timestamp)" >> $logfin
    exit 5
fi

# cleaning up and freeing up memory cache
/usr/bin/apt-get clean
/usr/bin/sync; echo 1 > /proc/sys/vm/drop_caches
