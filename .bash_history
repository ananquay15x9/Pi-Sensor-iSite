mkdir install
cp /media/pi/CARROTS/install/* install/
sudo apt list | grep chrom
sudo apt remove chromium-browser
sudo apt update
sudo apt list --upgradable
sudo apt remove vlc
sudo apt upgrade
sudo reboot
sudo vi /etc/lightdm/lightdm.conf 
ls
rm install/*
cp /media/pi/CARROTS/install/* install/
sudo apt update
sudo apt upgrade
sudo apt install openjdk-8-jdk
sudo apt purge openjdk-8-jre-headless
sudo apt install openjdk-8-jre-headless
sudo apt install openjfx
sudo apt instal dirmngr
sudo apt install dirmngr
sudo apt-key adv --keyserver keyserver.ubuntu.com --recv-keys E0B11894F66AEC98
sudo apt-key adv --keyserver keyserver.ubuntu.com --recv-keys 7638D0442B90D010
sudo apt-key adv --keyserver keyserver.ubuntu.com --recv-keys 8B48AD6246925553
echo 'deb http://httpredir.debian.org/debian stretch-backports main contrib non-free' | sudo tee -a /etc/apt/sources.list.d/debian-backports.list
sudo apt-get update
sudo apt -t stretch-backports install libsodium-dev
sudo apt-get install libusb-1.0-0-dev
git clone https://github.com/mvp/uhubctl.git
cd ub
cd uhubctl/
sudo make
sudo cp uhubctl /usr/local/bin
sudo vi /boot/config.txt 
sudo apt install ntp
sudo vi /etc/ntp.conf 
sudo systemctl daemon-reload
sudo systemctl stop systemd-timesyncd
sudo systemctl disable systemd-timesyncd
sudo mkdir /opt/isite
sudo chown -R pi:pi /opt/isite
cd /opt/isite
mkdir data
mkdir media
mkdir versions
cd versions/
ls
cp ~/install/ism-player-1.0.1.tar .
tar xvf ism-player-1.0.1.tar 
ls
ln -snvf ism-player-1.0.1 current
sudo adduser ism
sudo visudo /etc/sudoers/ism_player
man visudo
sudo visudo /etc/sudoers.d/ism_player
sudo visudo -f /etc/sudoers.d/ism_player
sudo vi /etc/environment 
sudo vi /etc/systemd/system/isite.service
sudo crontab -e
cat /etc/crontab 
ls /etc/cron.d
sudo crontab -e
ls
cat /etc/crontab 
cat /etc/cron.hourly/fake-hwclock 
ls /etc/cron.d/
ls -la /etc/cron.d/
ls /var/spool/cron/crontabs/
sudo ls /var/spool/cron/crontabs/
sudo ls /var/spool/cron/crontabs/root
sudo cat /var/spool/cron/crontabs/root
sudo vi /etc/lightdm/lightdm.conf
sudo reboot
sudo systemctl enable isite
sudo install ufw
sudo chmod o-r /etc/wpa_supplicant/wpa_supplicant.conf 
cat /etc/wpa_supplicant/wpa_supplicant.conf 
ls -la /etc/wpa_supplicant/wpa_supplicant.conf 
sudo vi /etc/wpa_supplicant/wpa_supplicant.conf 
sudo vi /etc/environment 
cd /opt/isite/
ls
sudo vi /etc/modules
sudo apt install watchdog
sudo vi /etc/watchdog.conf 
sudo systemctl enable watchdog
sudo systemctl start watchdog
sudo journalctl -u isite -f
sudo systemctl stop isite
cat /etc/environment 
cd /opt/isite/versions/
ls -la
rm -rf *
sudo rm -rf *
ls
sudo cp /home/ism/ism-player* .
ls
sudo chown pi:pi ism-player-1.0.1.tar 
tar xvf ism-player-1.0.1.tar 
ln -snvf ism-player-1.0.1 current
ls
ls -a
ls -la
cat ism-player-1.0.1
ls ism-player-1.0.1
cd ..
ls
ls data
rm data/*
ls -la
ls data
ls -la data
ls -la media/
sudo rm media/*
ls versions/
rm versions/ism-player-1.0.1.tar 
ls versions/
exit
sudo systemctl stop isite
sudo systemctl disable isite
cd /opt/isite/
ls
touch data/health.tmp 
sudo touch data/health.tmp 
ls -la versions/
ls
ps -ef | grep ntp
cd ~/
ls
ls -la
cd .config/
ls
sudo cp ~/install/isite_bg.png /home/ism/bg.png
sudo chown ism:sim /home/ism/bg.png
sudo chown ism:ism /home/ism/bg.png
sudo vi /etc/lightdm/lightdm.conf 
sudo apt install xscreensaver
sudo apt autoremove
cd /opt/isite/
ls
ls versions/
sudo touch data/health.tmp 
cat versions/current/check_player.sh 
sudo vi /etc/wpa_supplicant/wpa_supplicant.conf 
sudo vi /etc/environment 
sudo systemctl enable isite
exit
sudo systemctl stop isite
cd /opt/isite/
ls
ls -la versions/
sudo rm -rf data/*
sudo rm -rf media/*
ls
ls -la versions/
exit
sudo systemctl stop isite
sudo systemctl disable isite
sudo journalctl -u isite
ls
cd /opt/isite
ls
ls -la
ls -la dat
ls -la data
rm -rf data/*
rm -rf media/*
ls -la versions/
sudo crontab -e
sudo apt install unclutter
cat /etc/xdg/lxsession/LXDE-pi/autostart 
sudo vi /etc/xdg/lxsession/LXDE-pi/autostart 
sudo reboot
sudo uhubctl -a 0 -p 2 -l 1-1.1
sudo uhubctl
sudo uhubctl -a 1 -p 2 -l 1-1.1
cd /opt/isite/
cd versions/
cp /media/ism/CARROTS/install/ism-player.tar .
sudo cp /media/ism/CARROTS/install/ism-player.tar .
sudo cp /media/ism/CARROTS/install/ism-player-1.0.1.tar .
ls
cat current/config.properties
ls -la
ls ~/install/
ls -la ~/install/
rm -rf ism-player-1.0.1/
ls
tar -xvf ism-player-1.0.1.tar 
ls -la
mv ism-player-1.0.1.tar ~/install/
ls -la ~/install/
exit
sudo vi /etc/wpa_supplicant/wpa_supplicant.conf 
cd /opt/isite/
ls
sudo vi /etc/wpa_supplicant/wpa_supplicant.conf 
cd ~/
ls
ls install/
ls -la install/
sudo vi /etc/wpa_supplicant/wpa_supplicant.conf 
sudo systemctl enable isite
sudo crontab -e
sudo vi /etc/wpa_supplicant/wpa_supplicant.conf 
exit
sudo systemctl stop isite
sudo crontab -e
sudo systemctl disable isite
cd /opt/isite/
ls
rm -rf data/*
rm -rf media/*
ls
cd versions/
ls
rm -rf ism-player-1.0.1/
rm -rf 1.0.1-b6/
sudo rm -rf 1.0.1-b6/
ls
ls -la
sudo mv /home/ism/ism-player-1.0.1.tar ~/install/
ls
sudo chown pi:pi ~/install/ism-player-1.0.1.tar 
cp ~/install/ism-player-1.0.1.tar .
ls -la
tar xvf ism-player-1.0.1.tar 
ln -snvf ism-player-1.0.1 current
ls -la
rm ism-player-1.0.1.tar 
ls
cd ..
ls
mkdir bkup
cat versions/current/check_player.sh 
cp ~/install/ism-player-1.0.1.tar bkup/
ls bkup/
cd versions/current
ls
vi config.properties 
cd /opt/isite/
ls
ls bkup/
cd bkup/
ls
mv ism-player-1.0.1.tar ism-player.tar
ls
exit
passwd
sudo raspi-config
sudo vi /etc/environment 
exit
cd /opt/isite
ls
versions/current/scripts/build.sh 
versions/current/scripts/serial.sh 
sudo vi /etc/wpa_supplicant/wpa_supplicant.conf 
sudo versions/current/scripts/wifi_reset.sh 
cat versions/current/scripts/wifi_reset.sh 
sudo ifconfig wlan0 down
sudo ifconfig wlan0 up
sudo vi /etc/wpa_supplicant/wpa_supplicant.conf 
sudo reboot
lsusb
ifconfig
sudo apt-get install airmon-ng
sudo apt-get install aircrack-ng
sudo apt-get update
sudo apt-get install aircrack-ng
sudo apt-get install aircrack-ng --fix-missing
sudo vi /etc/wpa_supplicant/wpa_supplicant.conf 
ls
cd ~/
ls
cd python/
ls
cd probemon/
ls
sudo python probemon.py -i wlan<wlan number>mon -f -s -l
sudo airmon-ng start wlan1
sudo python probemon.py -i wlan<wlan number>mon -f -s -l
history | grep python
sudo python probemon.py -i wlan<wlan1 number>mon -f -s -l
ifconfig
sudo python probemon.py -i wlan<wlan number>mon -f -s -l
sudo python probemon.py
sudo python probemon.py -i wlan1 number>mon -f -s -l
sudo python probemon.py -i wlan1mon -f -s -l
sudo python probemon.py -i wlan1mon -f -s -I
sudo python probemon.py -i wlan1mon -f -s -l -r
vi probemon.py 
sudo python probemon.py -i wlan1mon -f -s -l -r
vi probemon.py 
sudo python probemon.py -i wlan1mon -f -s -l -r
vi probemon.py 
sudo python probemon.py -i wlan1mon -f -s -l -r
vi probemon.py 
sudo python probemon.py -i wlan1mon -f -s -l -r
vi probemon.py 
sudo python probemon.py -i wlan1mon -f -s -l -r
vi probemon.py 
sudo python probemon.py -i wlan1mon -f -s -l -r
vi probemon.py 
sudo python probemon.py -i wlan1mon -f -s -l -r
vi probemon.py 
sudo python probemon.py -i wlan1mon -f -s -l -r
vi probemon.py 
sudo python probemon.py -i wlan1mon -f -s -l -r
vi probemon.py 
sudo python probemon.py -i wlan1mon -f -s -l -r
cd ~/
ls
cd python/
ls
cd probemon/
ls
cat probemon.
cat probemon.py
vi probemon.py 
cd python/probemon/
ls
cp probemon.py probemon.bkup.py
sudo airmon-ng start wlan1
ifconfig
sudo python probemon.py -i wlan1mon
sudo airmon-ng start wlan1
cd ~/
ls
cd python/
ls
cd probemon/
ls
sudo python probemon.py
sudo python probemon.py -i wlan1mon
exit
which airmon
which airmon-ng
ls
cd python/
ls
cd probemon/
ls
sudo python probemon.py -i wlan1mon
sudo airmon-ng start wlan1
sudo python probemon.py -i wlan1mon
ls
cd ..
ls
cd probemon/
ls mon
ls
cd ..
ls
cd ..
ls
ls install/
sudo apt list
sudo apt list | grep mon
sudo apt list | grep airmon
sudo apt list | grep aircrack
sudo airmon-ng start wlan1
ls
cd /home/pi
ls
cd python/
ls
cd probemon/
ls
sudo python probemon.py -i wlan1mon
cd ~/
ls
cd python/
ls
cd netaddr/
ls
cd ..
sudo apt list | grep scapy
sudo apt list --installed | grep scapy
sudo apt list --installed | grep fuzzywuzzy
pip list
sudo cp -r netaddr /media/ism/CARROTS/
ls -la /media/ism/CARROTS
sudo ls -la /media/ism/CARROTS
cd ~/python/
ls
ls -la netaddr/
                                                                                                                                                                                                                                                                                           ls
curl -fsSL https://tailscale.com | sh
curl -fsSL https://tailscale.com/install.sh -o install-tailscale.sh
cat install-tailscale.sh
sudo sh install-tailscale.sh
cat /etc/os-release
curl -fsSL https://pkgs.tailscale.com/stable/raspbian/stretch.noarmor.gpg | sudo tee /usr/share/keyrings/tailscale-archive-keyring.gpg >/dev/null
curl -fsSL https://pkgs.tailscale.com/stable/raspbian/stretch.tailscale-keyring.list | sudo tee /etc/apt/sources.list.d/tailscale.list
sudo apt-get update
sudo apt-get install tailscale
sudo tailscale up
exit
